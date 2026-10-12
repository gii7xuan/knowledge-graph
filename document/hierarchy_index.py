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

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


DEFAULT_SPLITTER_VERSION = "hierarchy_v2_chunk1000_overlap100"
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


def sentence_spans(content: str, chunk_id: str) -> List[Dict[str, Any]]:
    sentences: List[Dict[str, Any]] = []
    for index, match in enumerate(SENTENCE_PATTERN.finditer(content)):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = match.start() + leading
        end = match.end() - trailing
        text = content[start:end]
        if not text:
            continue
        sentences.append(
            {
                "sentence_id": f"{chunk_id}_s{index}",
                "text": text,
                "start": start,
                "end": end,
            }
        )
    if not sentences and content.strip():
        fallback_start = len(content) - len(content.lstrip())
        fallback_end = len(content.rstrip())
        sentences.append(
            {
                "sentence_id": f"{chunk_id}_s0",
                "text": content[fallback_start:fallback_end],
                "start": fallback_start,
                "end": fallback_end,
            }
        )
    return sentences


def parent_breadcrumb(breadcrumb_path: str) -> Optional[str]:
    parts = [part.strip() for part in breadcrumb_path.split(">") if part.strip()]
    if len(parts) <= 1:
        return None
    return " > ".join(parts[:-1])


def split_markdown_text(
    text: str,
    document_id: str = "document",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    splitter_version: str = DEFAULT_SPLITTER_VERSION,
) -> List[Dict[str, Any]]:
    headers_to_split_on: List[Tuple[str, str]] = [
        ("#", "Header-1"),
        ("##", "Header-2"),
        ("###", "Header-3"),
        ("####", "Header-4"),
        ("#####", "Header-5"),
        ("######", "Header-6"),
    ]
    document_id = normalize_document_id(document_id)

    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )
    section_docs = markdown_splitter.split_text(text)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", ""],
    )
    final_docs = text_splitter.split_documents(section_docs)

    canonical_text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    document_version = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()

    chunks: List[Dict[str, Any]] = []
    node_occurrences: Dict[Tuple[Optional[str], str], int] = {}
    active_parts: List[str] = []
    active_nodes: List[Dict[str, Any]] = []
    search_from = 0
    for order, doc in enumerate(final_docs):
        metadata = dict(doc.metadata or {})
        breadcrumb_path = build_breadcrumb_path(metadata)
        parts = [part.strip() for part in breadcrumb_path.split(">") if part.strip()]
        common = 0
        while common < len(parts) and common < len(active_parts) and parts[common] == active_parts[common]:
            common += 1
        active_parts = active_parts[:common]
        active_nodes = active_nodes[:common]
        for depth in range(common, len(parts)):
            title = parts[depth]
            parent_id = active_nodes[-1]["section_id"] if active_nodes else None
            node_key = (parent_id, title)
            occurrence = node_occurrences.get(node_key, -1) + 1
            node_occurrences[node_key] = occurrence
            path = " > ".join(parts[:depth + 1])
            node = {
                "section_id": section_id_for(document_id, path, f"{parent_id or 'root'}:{occurrence}"),
                "breadcrumb_path": path, "parent_section_id": parent_id,
                "section_occurrence": occurrence, "header_level": depth + 1,
            }
            active_parts.append(title)
            active_nodes.append(node)
        raw_content = doc.page_content
        leading = len(raw_content) - len(raw_content.lstrip())
        trailing = len(raw_content) - len(raw_content.rstrip())
        content = raw_content[leading: len(raw_content) - trailing if trailing else len(raw_content)]
        if not active_nodes:
            active_nodes = [{
                "section_id": section_id_for(document_id, "正文", "root:0"),
                "breadcrumb_path": "正文", "parent_section_id": None,
                "section_occurrence": 0, "header_level": 0,
            }]
            active_parts = ["正文"]
        section_id = active_nodes[-1]["section_id"]
        parent_section_id = active_nodes[-1]["parent_section_id"]
        occurrence = active_nodes[-1]["section_occurrence"]
        chunk_id = chunk_id_for(document_id, breadcrumb_path, content, order)
        document_start = canonical_text.find(content, search_from)
        if document_start < 0:
            document_start = canonical_text.find(content)
        document_end = document_start + len(content) if document_start >= 0 else None
        if document_end is not None:
            search_from = max(search_from, document_end - chunk_overlap)
        sentences = sentence_spans(content, chunk_id)
        for sentence in sentences:
            sentence["chunk_start"] = sentence["start"]
            sentence["chunk_end"] = sentence["end"]
            sentence["document_start"] = (
                document_start + sentence["start"] if document_start >= 0 else None
            )
            sentence["document_end"] = (
                document_start + sentence["end"] if document_start >= 0 else None
            )
        chunks.append(
            {
                "document_id": document_id,
                "document_version": document_version,
                "chunk_id": chunk_id,
                "section_id": section_id,
                "parent_section_id": parent_section_id,
                "breadcrumb_path": breadcrumb_path,
                "header_level": header_level(metadata),
                "chunk_order": order,
                "prev_chunk_id": None,
                "next_chunk_id": None,
                "content": content,
                "sentences": sentences,
                "document_start": document_start if document_start >= 0 else None,
                "document_end": document_end,
                "offset_basis": "canonical_document" if document_start >= 0 else "chunk",
                "section_occurrence": occurrence,
                "section_ancestry": [dict(node) for node in active_nodes],
                "metadata": metadata,
                "splitter_version": splitter_version,
            }
        )

    for index, chunk in enumerate(chunks):
        if index > 0:
            chunk["prev_chunk_id"] = chunks[index - 1]["chunk_id"]
        if index + 1 < len(chunks):
            chunk["next_chunk_id"] = chunks[index + 1]["chunk_id"]
    return chunks


def build_document_index(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
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


def persist_document_index(index: Dict[str, Any], output_path: Any) -> Path:
    """Persist a portable index (lookup maps are reconstructed on load)."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "document_index_v2",
        "document_id": index.get("document_id"),
        "document_version": index.get("document_version"),
        "chunks": index.get("chunks", []),
        "sections": index.get("sections", []),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_document_index(path: Any) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_document_index(payload.get("chunks", []))
