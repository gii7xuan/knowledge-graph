"""Bounded, audited, ontology-guided extraction that retains source candidates."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from document.snapshot import read_snapshot, json_hash
from document.hierarchy_index import split_markdown_text, build_document_index
from document.index_integrity import validate_document_index
from schema.subontology_retriever import SubOntologyRetriever
from .candidate_contract import RESPONSE_SCHEMA, normalize_candidate

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extraction_v4.txt"


def prompt_template():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_scope(index, chunk, start=None, end=None, neighbors=2):
    start = chunk["core_start"] if start is None else start
    end = chunk["core_end"] if end is None else end
    sentences = [s for s in index["sentences"] if s["section_id"] == chunk["section_id"]]
    covered = [n for n, s in enumerate(sentences) if s["document_start"] < end and s["document_end"] > start]
    nearby = sentences[max(0, covered[0]-neighbors):covered[-1]+neighbors+1] if covered else []
    return {"source_chunk_id": chunk["chunk_id"], "section_id": chunk["section_id"],
            "core_start": start, "core_end": end,
            "context_start": min([start, *[s["document_start"] for s in nearby]]),
            "context_end": max([end, *[s["document_end"] for s in nearby]])}


def extract_source_scope(scope, *, index, ontology, llm_client, model, settings=None):
    settings = settings or {}
    template, prompt_hash = prompt_template()
    text = index["source_snapshot"]["canonical_text"]
    chunk = index["by_chunk_id"][scope["source_chunk_id"]]
    sub_schema = SubOntologyRetriever(ontology).retrieve(chunk["breadcrumb_path"], text[scope["context_start"]:scope["context_end"]])
    source = {"document_id": index["document_id"], "document_version": index["document_version"],
              "section": chunk["breadcrumb_path"], "scope": scope,
              "core_text": text[scope["core_start"]:scope["core_end"]],
              "context_text": text[scope["context_start"]:scope["context_end"]],
              "domain": settings.get("domain") or ontology.get("domain") or "default",
              "ontology_release": {key: (settings.get("release_binding") or {}).get(key)
                                   for key in ("ontology_release_id", "ontology_version", "profile_hash")}, "subontology": sub_schema,
              "filter_policy": settings.get("filter_policy") or {}, "output_schema": RESPONSE_SCHEMA}
    user_prompt = json.dumps(source, ensure_ascii=False)
    attempts = []
    for attempt in range(max(1, int(settings.get("request_attempts", 2)))):
        started = time.monotonic()
        record = {"scope": scope, "attempt": attempt+1, "model_requested": model,
                  "prompt_hash": prompt_hash, "request_hash": json_hash([template, source]),
                  "request_payload": source, "generation_settings": {"temperature": 0.0, "max_tokens": int(settings.get("max_tokens", 4096))}}
        record["status"] = "started"
        observer = settings.get("on_attempt")
        if observer: observer("started", record)
        try:
            response = llm_client.chat.completions.create(
                model=model, messages=[{"role": "system", "content": template}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"}, temperature=0.0, max_tokens=int(settings.get("max_tokens", 4096)))
            choice = response.choices[0]
            content = choice.message.content
            record.update(raw_response=content, model_returned=getattr(response, "model", None),
                          response_id=getattr(response, "id", None), finish_reason=getattr(choice, "finish_reason", None))
            usage = getattr(response, "usage", None)
            record["usage"] = {name: getattr(usage, name, None) for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                if record["finish_reason"] == "length":
                    record["status"] = "truncated"
                    return {"triplets": [], "coverage_status": "truncated", "attempts": [*attempts, record], "prompt_hash": prompt_hash}
                raise
            if not isinstance(payload, dict) or not isinstance(payload.get("triplets"), list):
                raise ValueError("invalid extraction response contract")
            coverage = payload.get("coverage_status", "unknown")
            if coverage not in {"complete_for_supplied_scope", "partial", "truncated", "unknown"}:
                coverage = "unknown"
            if record["finish_reason"] == "length": coverage = "truncated"
            record.update(status="completed" if coverage == "complete_for_supplied_scope" else "partial", coverage_status=coverage)
            return {"triplets": payload["triplets"], "coverage_status": coverage,
                    "attempts": [*attempts, record], "prompt_hash": prompt_hash}
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            attempts.append(record)
        finally:
            record["latency_ms"] = int((time.monotonic()-started)*1000)
            if record["status"] == "started": record["status"] = "interrupted"
            if observer: observer("finished", record)
    return {"triplets": [], "coverage_status": "failed", "attempts": attempts, "prompt_hash": prompt_hash}


def extract_candidates(index, ontology, release_binding, *, llm_client, model, run_id, settings=None):
    validate_document_index(index)
    settings = {**(settings or {}), "release_binding": release_binding}
    candidates, ledger, rejected_outputs, seen = [], [], [], set()
    text = index["source_snapshot"]["canonical_text"]
    max_calls = int(settings.get("max_model_calls", 64))
    used_calls = 0

    def visit(scope, depth=0):
        nonlocal used_calls
        if used_calls >= max_calls:
            ledger.append({"scope": scope, "status": "not_requested_budget", "depth": depth})
            return False
        bounded = {**settings, "request_attempts": min(int(settings.get("request_attempts", 2)), max_calls-used_calls)}
        response = extract_source_scope(scope, index=index, ontology=ontology, llm_client=llm_client, model=model, settings=bounded)
        used_calls += len(response["attempts"])
        entry = {"scope": scope, "depth": depth, "status": response["coverage_status"], "attempts": response["attempts"]}
        ledger.append(entry)
        malformed = False
        for raw in response["triplets"]:
            try:
                candidate = normalize_candidate(raw, index=index, scope=scope, ontology=ontology,
                    release_binding=release_binding, run_id=run_id, ordinal=len(candidates), prompt_hash=response["prompt_hash"])
            except (ValueError, TypeError, KeyError) as exc:
                rejected_outputs.append({"scope": scope, "raw": raw, "error": str(exc)})
                malformed = True
                continue
            anchors = candidate["evidence_items"]
            if anchors and not scope["core_start"] <= anchors[0]["start"] < scope["core_end"]:
                rejected_outputs.append({"scope": scope, "raw": raw, "reason": "context_only_not_source_owned"})
                continue
            fingerprint = json_hash([candidate["document_version"], candidate["triple"],
                                     [(a["start"], a["end"]) for a in anchors], None if anchors else scope])
            if fingerprint in seen:
                entry["duplicate_source_outputs"] = entry.get("duplicate_source_outputs", 0)+1
                continue
            seen.add(fingerprint)
            candidate["extraction_model"] = model
            last_attempt = response["attempts"][-1]
            candidate["extraction_model_returned"] = last_attempt.get("model_returned")
            candidate["extraction_response_id"] = last_attempt.get("response_id")
            candidates.append(candidate)
        if malformed: entry["status"] = "malformed_candidates"
        complete = response["coverage_status"] == "complete_for_supplied_scope" and not malformed
        if not complete and depth < int(settings.get("max_split_depth", 3)) and scope["core_end"]-scope["core_start"] > int(settings.get("min_scope_chars", 80)):
            midpoint = (scope["core_start"]+scope["core_end"])//2
            boundaries = [s["document_end"] for s in index["sentences"] if scope["core_start"] < s["document_end"] < scope["core_end"]]
            if boundaries: midpoint = min(boundaries, key=lambda value: abs(value-midpoint))
            chunk = index["by_chunk_id"][scope["source_chunk_id"]]
            left = visit(source_scope(index, chunk, scope["core_start"], midpoint), depth+1)
            right = visit(source_scope(index, chunk, midpoint, scope["core_end"]), depth+1)
            entry["recovered_by_subscopes"] = left and right
            return left and right
        return complete

    outcomes = []
    for ordinal, chunk in enumerate(index["chunks"]):
        scope = source_scope(index, chunk)
        title = chunk["breadcrumb_path"].split(" > ")[-1].strip()
        policy = settings.get("filter_policy") or {}
        reason = None
        if title in {"目录", "目 录"}: reason = "table_of_contents"
        elif title in {"习题", "练习题", "思考题"} and policy.get("exercise_sections", "defer") != "include": reason = "exercise_policy_deferred"
        elif not text[scope["core_start"]:scope["core_end"]].strip(): reason = "empty_scope"
        if settings.get("max_chunks") is not None and ordinal >= int(settings["max_chunks"]): reason = "not_requested_chunk_limit"
        if reason:
            ledger.append({"scope": scope, "status": reason})
            outcomes.append(reason in {"empty_scope", "table_of_contents", "exercise_policy_deferred"})
            continue
        outcomes.append(visit(scope))
    result = {"candidates": candidates, "requests": ledger, "malformed_or_context_outputs": rejected_outputs,
            "status": "completed_for_selected_policy" if all(outcomes) else "incomplete",
            "source_scope_count": len(index["chunks"]), "model_calls": used_calls,
            "model_coverage_is_not_proof": True}
    from evaluation.candidate_intake import intake_summary
    result["intake_metrics"] = intake_summary(candidates, result)
    return result


def run_v4_extraction(args, bridge):
    if bridge.ONTOLOGY_RELEASE is None:
        raise ValueError("candidate v4 requires a published ontology release")
    snapshot = read_snapshot(bridge.MARKDOWN_FILE_PATH)
    chunks = split_markdown_text(snapshot["canonical_text"], document_id=snapshot["document_id"],
                                chunk_size=600, chunk_overlap=80, snapshot=snapshot)
    index = build_document_index(chunks)
    release = bridge.ONTOLOGY_RELEASE
    run_id = "run_" + uuid4().hex
    import os
    run_id = os.environ.get("KG_RUN_ID") or run_id
    settings = {**(bridge.REVIEW_CONFIG.get("extraction") or {}), "max_chunks": args.max_chunks}
    pending = Path(bridge.project_root) / "runs" / "v3" / (run_id + ".pending")
    if (pending.parent / run_id).exists():
        raise FileExistsError("immutable extraction run already exists")
    pending.mkdir(parents=True, exist_ok=False)
    from document.hierarchy_index import persist_document_index
    persist_document_index(index, pending / "source_index.json")
    (pending / "scope_manifest.json").write_text(json.dumps([source_scope(index, chunk) for chunk in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
    state = {"run_id": run_id, "status": "running", "source_index_hash": index["index_content_hash"], **release.binding()}
    def save_state():
        (pending / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    save_state()
    def observe(event, record):
        with (pending / "requests.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, **record}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    settings["on_attempt"] = observe
    try:
        result = extract_candidates(index, release.ontology, release.binding(), llm_client=bridge.client,
                                    model=bridge.MODEL, run_id=run_id, settings=settings)
    except BaseException as exc:
        state.update(status="interrupted_or_failed", error_type=type(exc).__name__, error=str(exc))
        save_state()
        raise
    result["checkpoint_path"] = str(pending)
    try:
        manifest = bridge.write_v3_run(result["candidates"], index, release.ontology, run_id=run_id, extraction_report=result)
    except BaseException as exc:
        state.update(status="persistence_failed", error_type=type(exc).__name__, error=str(exc))
        save_state()
        raise
    state.update(status=result["status"], final_run_path=str(pending.parent / run_id))
    save_state()
    for target, payload in ((bridge.OUTPUT_JSON_PATH, []), (bridge.DISCARDED_TRIPLETS_PATH, [])):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    bridge.write_v2_outputs(result["candidates"], [], result["candidates"], [])
    evidence_path = Path(bridge.EVIDENCE_MD_PATH)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("# 来源候选引文（定位不代表语义通过）\n\n" + "\n\n".join(
        f"## {c['candidate_id']}\n" + "\n".join(c["evidence_quotes"]) for c in result["candidates"]), encoding="utf-8")
    print(json.dumps({"run_id": manifest["run_id"], "candidate_count": len(result["candidates"]),
                      "extraction_status": result["status"], "model_calls": result["model_calls"]}, ensure_ascii=False))
    return 0 if result["status"] == "completed_for_selected_policy" else 2
