"""Server-created manual bundles from a frozen proposal or canonical source."""
import json
import hashlib
from uuid import uuid4
from .admission import load_admission_artifacts, validate_admission
from .contracts import sha256_json
from .repository import ConflictError, UnprocessableEntityError
from evidence.verification import make_evidence_bundle, verify_evidence_bundle


def source_span(index, start, end):
    text = index["source_snapshot"]["canonical_text"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise UnprocessableEntityError("manual evidence offsets must lie in the canonical source")
    sections = sorted((s for s in index["sections"] if s.get("start", 0) <= start < end <= s.get("end", 0)), key=lambda s: s["end"]-s["start"])
    section = sections[0] if sections else None
    if not section: raise UnprocessableEntityError("manual evidence span must stay within one source section")
    chunks = [c for c in index["chunks"] if c["section_id"] == section["section_id"] and c["document_start"] < end and c["document_end"] > start]
    if not chunks: raise UnprocessableEntityError("manual evidence has no verified source chunks")
    identity = [index["document_id"], index["document_version"], "canonical_document", None, start, end]
    return {"evidence_id": "ev_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20],
        "document_id": index["document_id"], "document_version": index["document_version"],
        "source_snapshot_id": index["source_snapshot"]["snapshot_id"], "source_index_hash": index["index_content_hash"],
        "chunk_id": chunks[0]["chunk_id"], "section_id": section["section_id"], "sentence_id": None,
        "source_anchor_kind": "document_span", "span_chunk_ids": [c["chunk_id"] for c in chunks],
        "offset_basis": "canonical_document", "start": start, "end": end, "quote": text[start:end], "retrieval_level": "human_selected"}


def create_human_bundle(repository, request):
    candidate = repository.get_candidate(str(request.get("candidate_id") or ""))
    if candidate["revision"] != request.get("revision") or candidate["row_version"] != request.get("expected_row_version"):
        raise ConflictError("candidate revision/row_version changed before manual evidence selection")
    try: release, document = load_admission_artifacts(repository, candidate)
    except (ValueError, KeyError, OSError) as exc: raise UnprocessableEntityError("verified release and canonical source required: " + str(exc)) from exc
    items, assessments, material_counters, binding = [], [], set(), {}
    if request.get("audit_id"):
        from .proposal_resolver import selected_proposal
        audit = repository.get_audit(candidate["candidate_id"], request["audit_id"])
        if audit["status"] != "completed": raise UnprocessableEntityError("manual selection requires a completed exact proposal")
        selection = {**request, "selected_round_id": request.get("selected_round_id"),
                     "selected_proposal_digest": request.get("selected_proposal_digest"), "selected_evidence_hash": request.get("selected_evidence_hash")}
        selected = next((p for p in audit["rounds"] if p["round_id"] == selection["selected_round_id"]), None)
        if selected: selection["committee_version"] = selected["committee_version"]
        try: proposal, votes = selected_proposal(repository, candidate, selection)
        except (ValueError, KeyError) as exc: raise UnprocessableEntityError(str(exc)) from exc
        ids = request.get("evidence_ids")
        catalog = {e["evidence_id"]: e for e in proposal["evidence_bundle"]["items"]}
        if not isinstance(ids, list) or not ids or any(not isinstance(e, str) or e not in catalog for e in ids):
            raise UnprocessableEntityError("select nonempty evidence IDs from this exact candidate proposal")
        items = [catalog[e] for e in dict.fromkeys(ids)]
        material_counters = {e for vote in votes for e in vote.get("counter_evidence_ids", [])}
        if material_counters - set(ids): raise UnprocessableEntityError("material counterevidence must remain in the manual bundle")
        if any(i.get("requires_revision") and i.get("status", "open") != "resolved" for i in audit["context"].get("issue_history", [])):
            raise UnprocessableEntityError("candidate expression issues require a new revision before acceptance")
        binding = {k: selection.get(k) for k in ("audit_id", "selected_round_id", "selected_proposal_digest", "selected_evidence_hash", "committee_version")}
        assessments = request.get("evidence_assessments") or []
    else:
        spans = request.get("spans")
        if not isinstance(spans, list) or not 1 <= len(spans) <= 24: raise UnprocessableEntityError("provide 1..24 explicit source spans")
        for span in spans:
            if not isinstance(span, dict): raise UnprocessableEntityError("invalid manual source span")
            start, end = span.get("start"), span.get("end")
            if span.get("quote") is not None:
                quote = span["quote"]
                if not isinstance(quote, str) or not quote: raise UnprocessableEntityError("quote must be an exact nonempty source string")
                text, positions, offset = document["source_snapshot"]["canonical_text"], [], 0
                while (found := text.find(quote, offset)) >= 0:
                    positions.append(found); offset = found + 1
                occurrence = span.get("occurrence", 1 if len(positions) == 1 else None)
                if type(occurrence) is not int or not 1 <= occurrence <= len(positions):
                    raise UnprocessableEntityError("quote is absent/ambiguous; specify the exact occurrence (one based)")
                start, end = positions[occurrence - 1], positions[occurrence - 1] + len(quote)
            item = source_span(document, start, end)
            items.append(item)
            assessments.append({"evidence_id": item["evidence_id"], "role": span.get("role"), "note": span.get("note"),
                                "resolution_evidence_ids": span.get("resolution_evidence_ids", [])})
        # A manual source bundle cannot silently discard an unresolved model
        # counter from an earlier audit of this same revision.
        prior = repository.previous_revision_audit(candidate["candidate_id"], candidate["revision"], "")
        if prior:
            if any(i.get("requires_revision") and i.get("status", "open") != "resolved" for i in prior["context"].get("issue_history", [])):
                raise UnprocessableEntityError("candidate expression issues require a new revision before acceptance")
            material_counters = {e for v in prior["votes"] if v.get("response_status") == "ok" for e in v.get("counter_evidence_ids", [])}
            prior_items = {e["evidence_id"]: e for p in prior["rounds"] for e in p["evidence_bundle"]["items"]}
            selected_spans = {(e["start"], e["end"]) for e in items}
            if any(e not in prior_items or (prior_items[e]["start"], prior_items[e]["end"]) not in selected_spans for e in material_counters):
                raise UnprocessableEntityError("use the exact audited proposal to resolve its material counterevidence")
    verified = verify_evidence_bundle(make_evidence_bundle(items, ["human_selected"]), document, candidate)
    if not verified["items"] or verified.get("rejected_items"): raise UnprocessableEntityError("manual source anchor failed verification")
    ids = {e["evidence_id"] for e in verified["items"]}
    if not isinstance(assessments, list) or len(assessments) != len(ids): raise UnprocessableEntityError("provide one explicit semantic assessment per selected source")
    if {a.get("evidence_id") for a in assessments if isinstance(a, dict)} != ids: raise UnprocessableEntityError("assessment references must exactly match the selected bundle")
    support = {a["evidence_id"] for a in assessments if a.get("role") == "support"}
    for a in assessments:
        if not isinstance(a.get("role"), str) or a["role"] not in {"support", "counter", "context", "irrelevant"} or not isinstance(a.get("note"), str) or not a["note"].strip():
            raise UnprocessableEntityError("manual assessment requires an explicit role and concise source-based note")
        refs = a.get("resolution_evidence_ids") or []
        if not isinstance(refs, list) or any(not isinstance(e, str) or e not in ids for e in refs):
            raise UnprocessableEntityError("counter resolution references must belong to this evidence bundle")
        if a["evidence_id"] in material_counters and (a.get("role") == "counter" or not set(a.get("resolution_evidence_ids") or []) & support):
            raise UnprocessableEntityError("counterevidence requires an explicit scope resolution and supporting references")
    assessments = [{**a, "reviewer": request.get("reviewer", "local_operator"), "task_type": "human_triple_review",
                    "candidate_id": candidate["candidate_id"], "revision": candidate["revision"], **binding} for a in assessments]
    bundle = {"bundle_id": "human_" + uuid4().hex, "kind": "human", "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"], "input_row_version": candidate["row_version"], "candidate_hash": sha256_json(repository.get_revision(candidate["candidate_id"], candidate["revision"])),
        "ontology_release_id": candidate.get("ontology_release_id"), "profile_hash": candidate.get("profile_hash"),
        "document_version": candidate["document_version"], "source_index_hash": candidate.get("source_index_hash"),
        "evidence_bundle": verified, "assessments": assessments, "proposal_binding": binding,
        "reviewer": request.get("reviewer", "local_operator")}
    bundle["bundle_hash"] = sha256_json({k: v for k, v in bundle.items() if k not in {"bundle_id", "bundle_hash"}})
    repository.save_admission_bundle(bundle, expected_row_version=candidate["row_version"])
    return bundle
