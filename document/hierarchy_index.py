"""Build a hierarchical index for long Markdown documents.

The index keeps chunk, section, neighbour chunk, and sentence-span metadata so
later evidence retrieval can move from local sentences to wider document
context without losing provenance.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple



DEFAULT_SPLITTER_VERSION = "hierarchy_v3_chunk1000_overlap100"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 100
SENTENCE_PATTERN = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?", re.MULTILINE)


def normalize_document_id(value: str) -> str:
    base = re.sub(r"\.[^.]+$", "", str(value or "document"))
    base = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", base, flags=re.UNICODE).strip("_")
    return base or "document"


def build_breadcrumb_path(metadata: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("Header-1", "Header-2", "Header-3", "Header-4", "Header-5", "Header-6"):
        value = metadata.get(key)
        if value:
            parts.append(re.sub(r"^#+\s*", "", str(value).strip()))
    return " > ".join(parts) if parts else "正文"


def header_level(metadata: Dict[str, Any]) -> int:
    for level in range(6, 0, -1):
        if metadata.get(f"Header-{level}"):
            return level
    return 0


def section_id_for(document_id: str, breadcrumb_path: str, structural_position: str = "0") -> str:
    """Return a stable section id that also distinguishes repeated headings."""
    digest = hashlib.sha1(
        f"{document_id}\n{breadcrumb_path}\n{structural_position}".encode("utf-8")
    ).hexdigest()[:12]
    return f"section_{digest}"


def chunk_id_for(document_id: str, breadcrumb_path: str, content: str, order: int) -> str:
    digest = hashlib.sha1(
        f"{document_id}\n{breadcrumb_path}\n{order}\n{content}".encode("utf-8")
    ).hexdigest()[:12]
    return f"chunk_{digest}"




def parent_breadcrumb(breadcrumb_path: str) -> Optional[str]:
    parts = [part.strip() for part in breadcrumb_path.split(">") if part.strip()]
    if len(parts) <= 1:
        return None
    return " > ".join(parts[:-1])




def _build_lookup_maps(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_chunk_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    by_section_id: Dict[str, List[Dict[str, Any]]] = {}
    children_by_section_id: Dict[str, List[str]] = {}
    sections: Dict[str, Dict[str, Any]] = {}
    for chunk in chunks:
        by_section_id.setdefault(chunk["section_id"], []).append(chunk)
        for node in chunk.get("section_ancestry") or []:
            node_id = node["section_id"]
            sections.setdefault(
                node_id,
                {
                    "section_id": node_id,
                    "document_id": chunk.get("document_id"),
                    "document_version": chunk.get("document_version"),
                    "breadcrumb_path": node.get("breadcrumb_path", "正文"),
                    "parent_section_id": node.get("parent_section_id"),
                    "children_section_ids": [], "section_order": chunk.get("chunk_order", 0),
                    "text_start": chunk.get("document_start"), "text_end": chunk.get("document_end"),
                    "chunk_ids": [],
                },
            )
            parent_id = node.get("parent_section_id")
            if parent_id and node_id not in children_by_section_id.setdefault(parent_id, []):
                children_by_section_id[parent_id].append(node_id)
        sections.setdefault(
            chunk["section_id"],
            {
                "section_id": chunk["section_id"],
                "document_id": chunk.get("document_id"),
                "document_version": chunk.get("document_version"),
                "breadcrumb_path": chunk.get("breadcrumb_path", "正文"),
                "parent_section_id": chunk.get("parent_section_id"),
                "children_section_ids": [],
                "section_order": chunk.get("chunk_order", 0),
                "text_start": chunk.get("document_start"),
                "text_end": chunk.get("document_end"),
                "chunk_ids": [],
            },
        )
        section = sections[chunk["section_id"]]
        section["chunk_ids"].append(chunk["chunk_id"])
        starts = [value for value in (section.get("text_start"), chunk.get("document_start")) if value is not None]
        ends = [value for value in (section.get("text_end"), chunk.get("document_end")) if value is not None]
        section["text_start"] = min(starts) if starts else None
        section["text_end"] = max(ends) if ends else None
        parent = chunk.get("parent_section_id")
        if parent and chunk["section_id"] not in children_by_section_id.setdefault(parent, []):
            children_by_section_id[parent].append(chunk["section_id"])
            parent_path = parent_breadcrumb(chunk.get("breadcrumb_path", "")) or "正文"
            sections.setdefault(
                parent,
                {
                    "section_id": parent,
                    "document_id": chunk.get("document_id"),
                    "document_version": chunk.get("document_version"),
                    "breadcrumb_path": parent_path,
                    "parent_section_id": None,
                    "children_section_ids": [],
                    "section_order": chunk.get("chunk_order", 0),
                    "text_start": chunk.get("document_start"),
                    "text_end": chunk.get("document_end"),
                    "chunk_ids": [],
                },
            )
    for parent, children in children_by_section_id.items():
        if parent in sections:
            sections[parent]["children_section_ids"] = list(children)

    descendant_chunks: Dict[str, List[Dict[str, Any]]] = {}
    def collect(section_id: str, seen: Optional[set] = None) -> List[Dict[str, Any]]:
        seen = set(seen or ())
        if section_id in seen:
            return []
        seen.add(section_id)
        result = list(by_section_id.get(section_id, []))
        for child_id in children_by_section_id.get(section_id, []):
            result.extend(collect(child_id, seen))
        unique = {item["chunk_id"]: item for item in result}
        return sorted(unique.values(), key=lambda item: item.get("chunk_order", 0))

    for section_id in sections:
        descendant_chunks[section_id] = collect(section_id)
    return {
        "chunks": chunks,
        "document_id": chunks[0].get("document_id") if chunks else None,
        "document_version": chunks[0].get("document_version") if chunks else None,
        "by_chunk_id": by_chunk_id,
        "by_section_id": by_section_id,
        "children_by_section_id": children_by_section_id,
        "sections": sorted(sections.values(), key=lambda item: item.get("section_order", 0)),
        "sections_by_id": sections,
        "descendant_chunks_by_section_id": descendant_chunks,
    }






def sentence_spans(content, chunk_id):
    from .index_builder import sentence_ranges
    return [{"sentence_id": f"{chunk_id}_s{ordinal}", "text": content[left:right], "start": left, "end": right}
            for ordinal, (left, right) in enumerate(sentence_ranges(content))]


def split_markdown_text(text, document_id="document", chunk_size=DEFAULT_CHUNK_SIZE,
                        chunk_overlap=DEFAULT_CHUNK_OVERLAP, splitter_version=DEFAULT_SPLITTER_VERSION, *, snapshot=None):
    from .index_builder import build_chunks
    if splitter_version == DEFAULT_SPLITTER_VERSION:
        splitter_version = f"hierarchy_v3_chunk{chunk_size}_overlap{chunk_overlap}"
    return build_chunks(text, normalize_document_id(document_id), chunk_size, chunk_overlap, splitter_version, snapshot=snapshot)


def build_document_index(chunks, *, snapshot=None):
    from .index_integrity import finalize_index
    return finalize_index(_build_lookup_maps(chunks), chunks, snapshot=snapshot)


def persist_document_index(index, output_path):
    from .index_integrity import portable_index, validate_document_index
    if index.get("schema_version") == "document_index_v3":
        validate_document_index(index)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(portable_index(index), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_document_index(path, expected_hash=None, *, require_verified=False):
    from .index_integrity import load_index_payload
    return load_index_payload(json.loads(Path(path).read_text(encoding="utf-8")), expected_hash=expected_hash,
                              require_verified=require_verified)
