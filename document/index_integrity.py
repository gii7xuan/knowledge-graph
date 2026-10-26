"""Hash and verify canonical source, structure, sentence spans and chunk views."""

from copy import deepcopy
from .snapshot import json_hash, validate_snapshot
from .index_builder import parse_structure, sentence_ranges

FIELDS = ("schema_version", "document_id", "document_version", "source_snapshot", "source_sections",
          "sentences", "sections", "chunks", "structure_hash", "splitter_version")


def portable_index(index):
    if index.get("schema_version") != "document_index_v3":
        return {"schema_version": "document_index_v2", "integrity_status": "legacy_unverified",
                **{key: index.get(key) for key in ("document_id", "document_version", "chunks", "sections")}}
    return {**{key: index[key] for key in FIELDS}, "index_content_hash": index["index_content_hash"]}


def finalize_index(index, chunks, *, snapshot=None):
    snapshot = snapshot or getattr(chunks, "snapshot", None)
    if not snapshot:
        index["integrity_status"] = "legacy_unverified"
        return index
    validate_snapshot(snapshot)
    sections, segments = parse_structure(snapshot["canonical_text"], snapshot["document_id"])
    sentences = []
    for left, right, section in segments:
        for start, end in sentence_ranges(snapshot["canonical_text"], left, right):
            sentences.append({"sentence_id": "sentence_" + json_hash([snapshot["document_version"], start, end])[:20],
                              "document_start": start, "document_end": end,
                              "text": snapshot["canonical_text"][start:end], "section_id": section["section_id"]})
    indexed = index["sections_by_id"]
    for node in sections:
        indexed.setdefault(node["section_id"], {"chunk_ids": [], "children_section_ids": []}).update(deepcopy(node))
        indexed[node["section_id"]].update(text_start=node["start"], text_end=node["end"])
    index.update(schema_version="document_index_v3", document_id=snapshot["document_id"],
                 document_version=snapshot["document_version"], source_snapshot=deepcopy(snapshot),
                 source_sections=sections, sentences=sentences, sections=list(indexed.values()),
                 splitter_version=getattr(chunks, "splitter_version", chunks[0].get("splitter_version") if chunks else "empty"),
                 integrity_status="verified")
    index["structure_hash"] = json_hash({"sections": sections, "sentences": sentences})
    index["index_content_hash"] = json_hash({key: index[key] for key in FIELDS})
    validate_document_index(index)
    return index


def validate_document_index(index, expected_hash=None):
    if index.get("schema_version") != "document_index_v3":
        raise ValueError("verified source snapshot required; legacy index is unverified")
    snapshot = index["source_snapshot"]
    validate_snapshot(snapshot)
    if any(index.get(key) != snapshot[key] for key in ("document_id", "document_version")):
        raise ValueError("index/source identity mismatch")
    calculated = json_hash({key: index[key] for key in FIELDS})
    if calculated != index.get("index_content_hash") or expected_hash and calculated != expected_hash:
        raise ValueError("document index content hash mismatch")
    if json_hash({"sections": index["source_sections"], "sentences": index["sentences"]}) != index["structure_hash"]:
        raise ValueError("source structure hash mismatch")
    sections, _ = parse_structure(snapshot["canonical_text"], snapshot["document_id"])
    if sections != index["source_sections"]:
        raise ValueError("source headings/positions mismatch")
    text, seen = snapshot["canonical_text"], set()
    for chunk in index["chunks"]:
        start, end = chunk.get("document_start"), chunk.get("document_end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != chunk["content"] or chunk["chunk_id"] in seen):
            raise ValueError("chunk text/coordinates mismatch")
        seen.add(chunk["chunk_id"])
        if any(chunk.get(key) != snapshot[key] for key in ("document_id", "document_version")):
            raise ValueError("chunk source identity mismatch")
        for sentence in chunk["sentences"]:
            if (chunk["content"][sentence["start"]:sentence["end"]] != sentence["text"]
                    or sentence["document_start"] != start + sentence["start"]
                    or sentence["document_end"] != start + sentence["end"]):
                raise ValueError("sentence text/coordinates mismatch")
    return True


def load_index_payload(payload, expected_hash=None, require_verified=False):
    from .hierarchy_index import _build_lookup_maps
    if payload.get("schema_version") != "document_index_v3":
        if require_verified or expected_hash:
            raise ValueError("legacy document index cannot satisfy verified source binding")
        result = _build_lookup_maps(payload.get("chunks") or [])
        result["integrity_status"] = "legacy_unverified"
        return result
    validate_document_index(payload, expected_hash)
    result = _build_lookup_maps(payload["chunks"])
    result.update({key: deepcopy(payload[key]) for key in (*FIELDS, "index_content_hash")})
    result["sections_by_id"] = {node["section_id"]: node for node in result["sections"]}
    result["integrity_status"] = "verified"
    return result
