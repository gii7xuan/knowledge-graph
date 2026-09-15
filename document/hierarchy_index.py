"""Build a hierarchical index for long Markdown documents.

The index keeps chunk, section, neighbour chunk, and sentence-span metadata so
later evidence retrieval can move from local sentences to wider document
context without losing provenance.
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


DEFAULT_SPLITTER_VERSION = "hierarchy_v1_chunk1000_overlap100"
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


def section_id_for(document_id: str, breadcrumb_path: str) -> str:
    digest = hashlib.sha1(f"{document_id}\n{breadcrumb_path}".encode("utf-8")).hexdigest()[:12]
    return f"section_{digest}"


def chunk_id_for(document_id: str, breadcrumb_path: str, content: str, order: int) -> str:
    digest = hashlib.sha1(
        f"{document_id}\n{breadcrumb_path}\n{order}\n{content}".encode("utf-8")
    ).hexdigest()[:12]
    return f"chunk_{digest}"


def sentence_spans(content: str, chunk_id: str) -> List[Dict[str, Any]]:
    sentences: List[Dict[str, Any]] = []
    for index, match in enumerate(SENTENCE_PATTERN.finditer(content)):
        text = match.group(0).strip()
        if not text:
            continue
        start = match.start()
        end = match.end()
        sentences.append(
            {
                "sentence_id": f"{chunk_id}_s{index}",
                "text": text,
                "start": start,
                "end": end,
            }
        )
    if not sentences and content.strip():
        sentences.append(
            {
                "sentence_id": f"{chunk_id}_s0",
                "text": content.strip(),
                "start": 0,
                "end": len(content),
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

    chunks: List[Dict[str, Any]] = []
    for order, doc in enumerate(final_docs):
        metadata = dict(doc.metadata or {})
        breadcrumb_path = build_breadcrumb_path(metadata)
        content = doc.page_content.strip()
        section_id = section_id_for(document_id, breadcrumb_path)
        parent_path = parent_breadcrumb(breadcrumb_path)
        parent_section_id = section_id_for(document_id, parent_path) if parent_path else None
        chunk_id = chunk_id_for(document_id, breadcrumb_path, content, order)
        chunks.append(
            {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "section_id": section_id,
                "parent_section_id": parent_section_id,
                "breadcrumb_path": breadcrumb_path,
                "header_level": header_level(metadata),
                "chunk_order": order,
                "prev_chunk_id": None,
                "next_chunk_id": None,
                "content": content,
                "sentences": sentence_spans(content, chunk_id),
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
    for chunk in chunks:
        by_section_id.setdefault(chunk["section_id"], []).append(chunk)
        parent = chunk.get("parent_section_id")
        if parent and chunk["section_id"] not in children_by_section_id.setdefault(parent, []):
            children_by_section_id[parent].append(chunk["section_id"])
    return {
        "chunks": chunks,
        "by_chunk_id": by_chunk_id,
        "by_section_id": by_section_id,
        "children_by_section_id": children_by_section_id,
    }
