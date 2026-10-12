"""Ontology-specific Markdown preprocessing and chunk filtering."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from document.hierarchy_index import (
        build_document_index,
        normalize_document_id,
        split_markdown_text,
    )
    from ontologyBuild.directory_guided_builder import build_module_index, clean_term
except ImportError:
    from document.hierarchy_index import build_document_index, normalize_document_id, split_markdown_text
    from directory_guided_builder import build_module_index, clean_term


SPLITTER_VERSION = "ontology_preprocess_v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_markdown(path: str | Path) -> str:
    value = Path(path).read_text(encoding="utf-8")
    value = value.replace("###HEADER#### ", "# ").replace("###HEADER####", "# ")
    value = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", value)
    value = re.sub(r"```[^\n]*\n|```", "", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return value.strip()


def normalize_heading_number(title: str) -> str:
    """Remove only complete heading numbering patterns and keep lexical text intact."""
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", str(title or "").strip())
    value = re.sub(r"^\s*第[\d一二三四五六七八九十百]+[章节篇部]\s*", "", value)
    value = re.sub(r"^\s*\d+(?:[.．]\d+){0,5}\s+", "", value)
    value = re.sub(r"^\s*[（(]?\d+[）).、]\s*", "", value)
    return value.strip()


def _breadcrumb_parts(chunk: dict[str, Any]) -> list[str]:
    return [part.strip() for part in str(chunk.get("breadcrumb_path") or "").split(">") if part.strip()]


def classify_chunk(chunk: dict[str, Any]) -> tuple[str, str]:
    parts = _breadcrumb_parts(chunk)
    root = normalize_heading_number(parts[0]) if parts else ""
    title = normalize_heading_number(parts[-1]) if parts else ""
    content = str(chunk.get("content") or "").strip()
    compact = re.sub(r"\s+", "", content)

    if not compact:
        return "exclude", "empty"
    if re.fullmatch(r"[\d\-—_·.．…\s]+", compact):
        return "exclude", "page_or_separator"
    if re.search(r"^(封面|版权|出版|CIP|作者简介|总序|前言|序言)$", root):
        return "exclude", "front_matter"
    if re.search(r"^(目录|目\s*录)$", root) or re.search(r"^(目录|目\s*录)$", title):
        return "exclude", "table_of_contents"
    if re.search(r"^(参考文献|索引|附录)$", root):
        return "exclude", "reference_or_index"
    if re.search(r"^(习题|思考题|练习题)$", title):
        return "review", "exercise_section"
    if re.fullmatch(r"(?:[#*\s\dA-Za-z.．,，;；:：()（）+\-*/=<>_^{}\\$]+)", compact):
        return "exclude", "formula_or_symbol_only"
    return "include", "body"


def preprocess_markdown(markdown_path: str | Path, *, max_chunks: int | None = None) -> dict[str, Any]:
    path = Path(markdown_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到 Markdown 文件: {path}")
    text = read_markdown(path)
    document_id = normalize_document_id(path.name)
    chunks = split_markdown_text(text, document_id=document_id, splitter_version=SPLITTER_VERSION)

    chunk_report = []
    included = []
    for chunk in chunks:
        decision, reason = classify_chunk(chunk)
        record = {
            "chunk_id": chunk.get("chunk_id"),
            "section_id": chunk.get("section_id"),
            "breadcrumb_path": chunk.get("breadcrumb_path"),
            "decision": decision,
            "reason": reason,
        }
        chunk_report.append(record)
        if decision == "include":
            enriched = dict(chunk)
            enriched["source_document_sha256"] = sha256_text(text)
            included.append(enriched)

    if max_chunks is not None:
        included = included[:max_chunks]

    module_index = build_module_index(included or chunks)
    document_index = build_document_index(chunks)
    return {
        "document_id": document_id,
        "source_path": str(path),
        "source_sha256": sha256_text(text),
        "generated_at": now_iso(),
        "all_chunks": chunks,
        "included_chunks": included,
        "module_index": module_index,
        "document_index": {
            "chunks": document_index["chunks"],
            "children_by_section_id": document_index["children_by_section_id"],
        },
        "chunk_filter_report": {
            "generated_at": now_iso(),
            "splitter_version": SPLITTER_VERSION,
            "total_chunks": len(chunks),
            "included_chunks": len(included),
            "excluded_chunks": sum(1 for item in chunk_report if item["decision"] == "exclude"),
            "review_chunks": sum(1 for item in chunk_report if item["decision"] == "review"),
            "items": chunk_report,
        },
    }


def write_preprocess_artifacts(data: dict[str, Any], run_dir: str | Path) -> None:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("document_index.json", data["document_index"]),
        ("chunk_filter_report.json", data["chunk_filter_report"]),
        ("ontology_modules.json", data["module_index"]),
    ):
        (run_path / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
