"""Unified ontology extraction pipeline.

This is the default entry point for ontology candidate extraction. It keeps run
artifacts isolated under runs/ontology/<run_id>/ and writes only compatibility
snapshots for existing review pages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ontologyBuild.base_ontology_audit import audit_ontology
from ontologyBuild.candidate_validator import (
    empty_candidates,
    validate_candidate_batch,
    write_validation_artifacts,
)
from ontologyBuild.config import (
    OntologyPipelineConfig,
    create_llm_client,
    public_config,
)
from ontologyBuild.context_builder import (
    OntologyContextBuilder,
    load_ontology,
)
from ontologyBuild.document_preprocessor import (
    preprocess_markdown,
    write_preprocess_artifacts,
)
from ontologyBuild.ontology_candidate import (
    ChunkExtractionError,
    extract_candidates_from_chunk,
)
from ontologyBuild.ontology_normalizer import normalize_candidate_data
from ontologyBuild.review_module import review_candidates


ROOT = PROJECT_ROOT
RUNS_DIR = ROOT / "runs" / "ontology"
BASE_ONTOLOGY_PATH = ROOT / "ontology" / "ontology.json"
LEGACY_CANDIDATES_PATH = ROOT / "ontologyBuild" / "ontology_candidates.json"
LEGACY_RAW_CANDIDATES_PATH = ROOT / "ontologyBuild" / "ontology_candidates_raw.json"
LEGACY_REVIEW_RESULT_PATH = ROOT / "ontologyBuild" / "ontology_review_result.json"
LEGACY_REVIEW_LOG_PATH = ROOT / "ontologyBuild" / "ontology_review_log.json"
LEGACY_BUILD_MODULES_PATH = ROOT / "ontologyBuild" / "ontology_modules.json"
LEGACY_ONTOLOGY_MODULES_PATH = ROOT / "ontology" / "ontology_modules.json"
LEGACY_TOPIC_MAPPING_PATH = ROOT / "ontology" / "topic_schema_mapping.json"
LEGACY_ONTOLOGY_PREVIEW_PATH = ROOT / "ontologyBuild" / "ontology_preview.json"
CURRENT_POINTER_PATH = RUNS_DIR / "current.json"

RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_candidate_id(run_id: str, section: str, item: dict[str, Any], index: int) -> str:
    evidence = item.get("evidence") or []
    evidence_text = ""
    if evidence and isinstance(evidence[0], dict):
        evidence_text = f"{evidence[0].get('chunk_id', '')}|{evidence[0].get('text', '')}"
    name = item.get("name") or item.get("property_name") or item.get("text_mention") or ""
    endpoints = f"{item.get('domain_candidate', '')}|{item.get('range_candidate', '')}|{item.get('range_datatype', '')}"
    digest = hashlib.sha1(f"{run_id}\n{section}\n{name}\n{endpoints}\n{evidence_text}\n{index}".encode("utf-8")).hexdigest()[:12]
    return f"{section}_{digest}"


def attach_run_metadata(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    chunks_by_id: dict[str, dict[str, Any]],
    module_index: dict[str, Any],
    run_id: str,
) -> dict[str, list[dict[str, Any]]]:
    for section in RESULT_FIELDS:
        for index, item in enumerate(candidates.get(section, []) or []):
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence") or []
            chunk_id = evidence[0].get("chunk_id") if evidence and isinstance(evidence[0], dict) else None
            chunk = chunks_by_id.get(chunk_id, {})
            item.setdefault("source_module", module_index.get("chunks", {}).get(chunk_id))
            item.setdefault("source_section_id", chunk.get("section_id"))
            item.setdefault("source_document_id", chunk.get("document_id"))
            item.setdefault("candidate_revision", 1)
            item.setdefault("run_id", run_id)
            item.setdefault("candidate_id", stable_candidate_id(run_id, section, item, index))
    return candidates


def merge_candidate_batches(batches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = empty_candidates()
    for batch in batches:
        for field in RESULT_FIELDS:
            result[field].extend(batch.get(field, []) or [])
    return result


class _MockMessage:
    def __init__(self, content: str):
        self.content = content


class _MockChoice:
    def __init__(self, content: str):
        self.message = _MockMessage(content)


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class MockOntologyClient:
    """Offline client for tests and smoke checks."""

    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                prompt = "\n".join(message.get("content", "") for message in kwargs.get("messages", []))
                chunk_id = "mock_chunk"
                marker = "chunk_id:"
                if marker in prompt:
                    chunk_id = prompt.split(marker, 1)[1].splitlines()[0].strip() or chunk_id
                chunk_text = prompt.split("chunk_text:", 1)[-1]
                evidence = chunk_text.strip().splitlines()[0].strip() if chunk_text.strip() else ""
                name = ""
                for pattern in (
                    r"([\u4e00-\u9fa5A-Za-z0-9]{2,24})(?:是指|定义为|称为)",
                    r"(?:称为|叫作|叫做)([\u4e00-\u9fa5A-Za-z0-9]{2,24})",
                ):
                    import re

                    match = re.search(pattern, chunk_text)
                    if match:
                        name = match.group(1).strip()
                        break
                result = empty_candidates()
                evidence = ""
                for line in chunk_text.splitlines():
                    line = line.strip()
                    if name and name in line:
                        evidence = line
                        break
                if name and evidence:
                    result["candidate_classes"].append(
                        {
                            "name": name,
                            "normalized_name": name,
                            "parent_candidate": None,
                            "definition_candidate": evidence,
                            "status": "new_candidate",
                            "matched_existing_class": None,
                            "evidence": [{"chunk_id": chunk_id, "text": evidence}],
                            "confidence": 0.7,
                            "reason": "mock client 从定义句生成候选。",
                        }
                    )
                return _MockResponse(json.dumps(result, ensure_ascii=False))

        def __init__(self):
            self.completions = self._Completions()

    def __init__(self):
        self.chat = self._Chat()


def create_run_dir(run_id: str) -> Path:
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "raw_responses").mkdir(parents=True, exist_ok=True)
    return run_dir


def snapshot_inputs(run_dir: Path, markdown_path: Path, ontology_path=None) -> dict[str, Any]:
    ontology_path=Path(ontology_path) if ontology_path is not None else BASE_ONTOLOGY_PATH
    snapshot = {
        "markdown": {
            "path": str(markdown_path),
            "sha256": sha256_file(markdown_path),
        },
        "ontology": {
            "path": str(ontology_path),
            "sha256": sha256_file(ontology_path) if ontology_path.exists() else "",
        },
    }
    if ontology_path.exists():
        shutil.copy2(ontology_path, run_dir / "ontology_snapshot.json")
    return snapshot


def extraction_client(config: OntologyPipelineConfig, llm_client=None):
    if llm_client is not None:
        return llm_client
    if config.extraction_engine == "mock":
        return MockOntologyClient()
    return create_llm_client(config)


def extract_with_llm(
    chunks: list[dict[str, Any]],
    *,
    config: OntologyPipelineConfig,
    client,
    context_builder: OntologyContextBuilder,
    module_index: dict[str, Any],
    run_id: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    chunk_status = []
    legal_empty = 0
    retry_count = config.max_retries + 1
    total_chunks = len(chunks)
    for chunk_index, chunk in enumerate(chunks, start=1):
        context = context_builder.for_chunk(chunk)
        chunk_state = "failed"
        candidate_count = 0
        try:
            chunk_result = extract_candidates_from_chunk(
                chunk,
                retry_count=retry_count,
                ontology_context=context,
                llm_client=client,
                model=config.model or "mock-model",
                max_output_tokens=config.max_output_tokens,
                adaptive_retry_chunk_size=config.adaptive_retry_chunk_size,
                adaptive_retry_chunk_overlap=config.adaptive_retry_chunk_overlap,
            )
            count = sum(len(chunk_result.get(field, [])) for field in RESULT_FIELDS)
            candidate_count = count
            if count == 0:
                legal_empty += 1
            batches.append(chunk_result)
            chunk_state = "succeeded_empty" if count == 0 else "succeeded"
            chunk_status.append({"chunk_id": chunk["chunk_id"], "status": chunk_state, "candidate_count": count})
        except ChunkExtractionError as exc:
            chunk_status.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "status": "failed",
                    "error": str(exc),
                    "attempts": exc.attempts,
                }
            )
        except Exception as exc:
            chunk_status.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "status": "failed",
                    "error": str(exc),
                }
            )
        print(
            f"[ontology] {chunk_index}/{total_chunks} "
            f"chunk={chunk['chunk_id']} status={chunk_state} candidates={candidate_count}",
            flush=True,
        )
    stats = {
        "total_chunks": len(chunks),
        "successful_chunks": sum(1 for item in chunk_status if item["status"] in {"succeeded", "succeeded_empty"}),
        "failed_chunks": sum(1 for item in chunk_status if item["status"] == "failed"),
        "legal_empty_chunks": legal_empty,
    }
    return merge_candidate_batches(batches), chunk_status, stats


def run_status_from_stats(stats: dict[str, int], candidate_count: int) -> str:
    total = stats.get("total_chunks", 0)
    failed = stats.get("failed_chunks", 0)
    success = stats.get("successful_chunks", 0)
    if total == 0:
        return "failed"
    if failed == total:
        return "failed"
    if failed:
        return "partial"
    if success == total and candidate_count == 0:
        return "succeeded_empty"
    return "succeeded"


def build_review_result(candidates: dict[str, Any], *, review_mode: str, reviewer: str, run_id: str) -> dict[str, Any]:
    flat = []
    type_map = {
        "candidate_classes": "class",
        "candidate_object_properties": "object_property",
        "candidate_datatype_properties": "datatype_property",
        "candidate_constraints": "constraint",
        "ontology_mapping": "mapping",
        "uncertain_candidates": "uncertain",
    }
    for section, candidate_type in type_map.items():
        for item in candidates.get(section, []) or []:
            if isinstance(item, dict):
                copied = dict(item)
                copied.setdefault("candidate_type", candidate_type)
                copied.setdefault("run_id", run_id)
                copied.setdefault("candidate_revision", 1)
                flat.append(copied)
    result = review_candidates(flat, review_mode=review_mode, reviewer=reviewer)
    result["run_id"] = run_id
    result["generated_at"] = now_iso()
    result["note"] = "已通过只表示候选审核通过，尚未发布到正式 ontology/ontology.json。"
    return result


def write_compatibility_outputs(
    *,
    normalized: dict[str, Any],
    raw_candidates: dict[str, Any],
    review_result: dict[str, Any],
) -> None:
    write_json(LEGACY_RAW_CANDIDATES_PATH, raw_candidates)
    write_json(LEGACY_CANDIDATES_PATH, normalized["candidates"])
    write_json(LEGACY_BUILD_MODULES_PATH, normalized["modules"])
    write_json(LEGACY_ONTOLOGY_MODULES_PATH, normalized["modules"])
    write_json(LEGACY_TOPIC_MAPPING_PATH, normalized["topic_mapping"])
    write_json(LEGACY_ONTOLOGY_PREVIEW_PATH, normalized["ontology_preview"])
    write_json(LEGACY_REVIEW_RESULT_PATH, review_result)
    write_json(LEGACY_REVIEW_LOG_PATH, review_result.get("review_log", []))


def run_ontology_pipeline(
    markdown_path: str | Path,
    config: OntologyPipelineConfig | None = None,
    *,
    llm_client=None,
    progress_callback=None,
    write_compatibility: bool = True,
    base_ontology_path=None,
) -> dict[str, Any]:
    config = config or OntologyPipelineConfig.from_env()
    markdown_path = Path(markdown_path).resolve()
    if not markdown_path.exists() or markdown_path.suffix.lower() != ".md":
        raise ValueError("请选择存在的 Markdown 文档")

    run_id = f"{now_stamp()}_{hashlib.sha1(str(markdown_path).encode('utf-8')).hexdigest()[:8]}"
    run_dir = create_run_dir(run_id)
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "markdown_path": str(markdown_path),
        "config": public_config(config),
        "outputs": {},
        "stage_status": {},
        "errors": [],
    }
    write_json(run_dir / "manifest.json", manifest)

    try:
        base_path=Path(base_ontology_path).resolve() if base_ontology_path is not None else BASE_ONTOLOGY_PATH
        if not base_path.exists() and config.base_policy=='draft':
            # A cleared installation can generate an initial draft. The empty
            # seed is run-scoped and is never registered as a published ontology.
            base_path=run_dir/'bootstrap_base.json'
            write_json(base_path,{'domain':'document_ontology_draft','classes':{},'object_properties':{},'data_properties':{}})
            manifest['base_origin']='empty_run_scoped_bootstrap'
            manifest['published_base_used']=False
        manifest["input_snapshot"] = snapshot_inputs(run_dir, markdown_path,base_path)
        audit = audit_ontology(base_path)
        write_json(run_dir / "base_audit.json", audit)
        manifest["stage_status"]["base_audit"] = "passed" if audit["passed"] else "failed"
        if config.base_policy == "strict" and not audit["passed"]:
            raise RuntimeError("基础本体审计未通过；可修复本体后重试，或显式设置 ONTOLOGY_BASE_POLICY=draft 生成候选草案")

        preprocess = preprocess_markdown(
            markdown_path,
            max_chunks=config.max_chunks,
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
        write_preprocess_artifacts(preprocess, run_dir)
        manifest["stage_status"]["document_preprocess"] = "passed"
        chunks = preprocess["included_chunks"]
        if not chunks:
            raise RuntimeError("文档过滤后没有可抽取正文片段")

        ontology = load_ontology(base_path)
        context_builder = OntologyContextBuilder(
            ontology,
            ontology_path=base_path,
            max_chars=config.context_max_chars,
            document_id=preprocess["document_id"],
        )
        context_summary = context_builder.summary()
        write_json(run_dir / "context_summary.json", context_summary)
        manifest["context_summary"] = context_summary
        manifest["stage_status"]["context_build"] = "passed"

        client = extraction_client(config, llm_client=llm_client)
        raw_candidates, chunk_status, extraction_stats = extract_with_llm(
            chunks,
            config=config,
            client=client,
            context_builder=context_builder,
            module_index=preprocess["module_index"],
            run_id=run_id,
        )
        candidate_count = sum(len(raw_candidates.get(field, [])) for field in RESULT_FIELDS)
        extraction_status = run_status_from_stats(extraction_stats, candidate_count)
        write_json(run_dir / "raw_candidates.json", raw_candidates)
        write_json(run_dir / "raw_responses" / "chunk_status.json", chunk_status)
        manifest["stage_status"]["candidate_extraction"] = extraction_status
        manifest["extraction_stats"] = extraction_stats
        if extraction_status == "failed":
            raise RuntimeError("所有文档片段抽取失败，已保留失败清单")

        validation = validate_candidate_batch(raw_candidates, chunks, ontology_path=base_path)
        write_validation_artifacts(validation, run_dir)
        manifest["stage_status"]["candidate_validation"] = "passed" if validation["passed"] else "partial"

        validated_candidates = validation.get("validated_candidates", empty_candidates())
        validated_candidates = attach_run_metadata(
            validated_candidates,
            chunks_by_id={chunk["chunk_id"]: chunk for chunk in chunks},
            module_index=preprocess["module_index"],
            run_id=run_id,
        )
        write_json(run_dir / "validated_candidates.json", validated_candidates)

        normalized = normalize_candidate_data(
            ontology,
            validated_candidates,
            preprocess["module_index"],
            corpus_role=config.corpus_role,
            allow_new_concepts=config.auto_accept_new,
        )
        write_json(run_dir / "normalized_candidates.json", normalized["candidates"])
        write_json(run_dir / "ontology_modules.json", normalized["modules"])
        write_json(run_dir / "topic_schema_mapping.json", normalized["topic_mapping"])
        write_json(run_dir / "ontology_preview.json", normalized["ontology_preview"])
        manifest["stage_status"]["normalization"] = "passed"

        review_result = build_review_result(
            normalized["candidates"],
            review_mode=config.review_mode,
            reviewer="system",
            run_id=run_id,
        )
        write_json(run_dir / "review_result.json", review_result)
        (run_dir / "review_log.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in review_result.get("review_log", [])),
            encoding="utf-8",
        )
        manifest["stage_status"]["review_queue"] = "passed"

        if write_compatibility:
            write_compatibility_outputs(
                normalized=normalized,
                raw_candidates=raw_candidates,
                review_result=review_result,
            )

        manifest["status"] = extraction_status
        manifest["finished_at"] = now_iso()
        manifest["outputs"] = {
            "run_dir": str(run_dir),
            "raw_candidates": str(run_dir / "raw_candidates.json"),
            "validated_candidates": str(run_dir / "validated_candidates.json"),
            "review_result": str(run_dir / "review_result.json"),
        }
        if write_compatibility:
            manifest["outputs"]["legacy_candidates"] = str(LEGACY_CANDIDATES_PATH)
            manifest["outputs"]["legacy_review_result"] = str(LEGACY_REVIEW_RESULT_PATH)
        manifest["candidate_counts"] = normalized["candidate_counts"]
        write_json(run_dir / "manifest.json", manifest)
        write_json(CURRENT_POINTER_PATH, {"run_id": run_id, "run_dir": str(run_dir), "manifest": str(run_dir / "manifest.json")})
        if progress_callback:
            progress_callback(manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["finished_at"] = now_iso()
        manifest["errors"].append({"message": str(exc), "type": type(exc).__name__})
        write_json(run_dir / "manifest.json", manifest)
        if progress_callback:
            progress_callback(manifest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="运行本体候选抽取流水线")
    parser.add_argument("--input", default=os.environ.get("ONTOLOGY_MARKDOWN_FILE", ""), help="Markdown 文档路径")
    parser.add_argument("--mock", action="store_true", help="使用离线 mock client，不调用真实模型")
    parser.add_argument("--max-chunks", type=int, default=None, help="限制抽取片段数")
    parser.add_argument("--base-policy", choices=["strict", "draft"], default=None)
    parser.add_argument("--write-current", action="store_true", help="同时更新当前工作台兼容 JSON")
    args = parser.parse_args()
    if not args.input:
        raise SystemExit("请通过 --input 或 ONTOLOGY_MARKDOWN_FILE 指定 Markdown 文档")
    overrides: dict[str, Any] = {}
    if args.mock:
        overrides["extraction_engine"] = "mock"
        overrides["model"] = "mock-model"
    if args.max_chunks is not None:
        overrides["max_chunks"] = args.max_chunks
    if args.base_policy:
        overrides["base_policy"] = args.base_policy
    config = OntologyPipelineConfig.from_env(**overrides)
    manifest = run_ontology_pipeline(args.input, config, write_compatibility=args.write_current)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
