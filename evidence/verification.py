"""Deterministic evidence anchor and bundle validation."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from review_agent.contracts import EVIDENCE_BUNDLE_VERSION, canonical_json


def span_key(item: Mapping[str, Any]) -> Tuple[Any, ...]:
    document_id = item.get("document_id")
    document_version = item.get("document_version")
    basis = item.get("offset_basis") or "chunk"
    if basis == "canonical_document" and item.get("start") is not None and item.get("end") is not None:
        return (document_id, document_version, basis, item["start"], item["end"])
    return (
        document_id, document_version, item.get("chunk_id"), item.get("sentence_id"),
        item.get("chunk_start", item.get("start")), item.get("chunk_end", item.get("end")),
        item.get("quote") or item.get("text") or "",
    )


def verify_anchor(item: Mapping[str, Any], document_or_chunk: str) -> Dict[str, Any]:
    quote = str(item.get("quote") or item.get("text") or "")
    start = item.get("start")
    end = item.get("end")
    if not quote:
        return {"valid": False, "status": "empty_quote", "actual_quote": ""}
    if start is None or end is None:
        return {"valid": False, "status": "missing_offset", "actual_quote": ""}
    if type(start) is not int or type(end) is not int:
        return {"valid": False, "status": "invalid_offset", "actual_quote": ""}
    if start < 0 or end <= start or end > len(document_or_chunk):
        return {"valid": False, "status": "out_of_range", "actual_quote": ""}
    actual = document_or_chunk[start:end]
    return {"valid": actual == quote, "status": "exact" if actual == quote else "mismatch", "actual_quote": actual}


def deduplicate_evidence(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for raw in items:
        item = dict(raw)
        key = span_key(item)
        current = merged.get(key)
        level = item.get("retrieval_level") or item.get("level")
        if current is None:
            if level:
                item["retrieved_via_levels"] = [level]
            else:
                item.setdefault("retrieved_via_levels", [])
            item.setdefault("semantic_status", "unknown")
            merged[key] = item
            continue
        levels = current.setdefault("retrieved_via_levels", [])
        for value in [level, *(item.get("retrieved_via_levels") or [])]:
            if value and value not in levels:
                levels.append(value)
        current["retrieval_score"] = max(
            float(current.get("retrieval_score") or 0.0), float(item.get("retrieval_score") or 0.0)
        )
    return list(merged.values())


def make_evidence_bundle(items: Iterable[Mapping[str, Any]], searched_levels: Iterable[str]) -> Dict[str, Any]:
    unique = deduplicate_evidence(items)
    source_groups = {
        (item.get("document_id"), item.get("document_version")) for item in unique if item.get("document_id")
    }
    payload = {
        "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
        "items": unique,
        "searched_levels": list(dict.fromkeys(searched_levels)),
        "retrieved_unique_span_count": len(unique),
        "independent_document_count": len(source_groups),
    }
    payload["evidence_hash"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return payload


def verify_evidence_bundle(bundle: Mapping[str, Any], document_snapshot: Mapping[str, Any],
                           candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Only exact frozen-source anchors may enter a round's reference catalogue."""
    if document_snapshot.get("schema_version") == "document_index_v3":
        from document.index_integrity import validate_document_index
        validate_document_index(document_snapshot, candidate.get("source_index_hash"))
    chunks = document_snapshot.get("by_chunk_id") or {
        item["chunk_id"]: item for item in document_snapshot.get("chunks", [])
    }
    groups: Dict[str, set] = {}
    for item in bundle.get("items") or []:
        identity = {key: item.get(key) for key in (
            "document_id", "document_version", "offset_basis", "start", "end", "quote")}
        if item.get("offset_basis") != "canonical_document":
            identity["chunk_id"] = item.get("chunk_id")
        signature = canonical_json(identity)
        groups.setdefault(str(item.get("evidence_id") or ""), set()).add(signature)
    valid, rejected = [], list(bundle.get("rejected_items") or [])
    for raw in bundle.get("items") or []:
        item = dict(raw)
        chunk = chunks.get(item.get("chunk_id"))
        status = "exact"
        if not item.get("evidence_id"):
            status = "missing_evidence_id"
        elif len(groups[str(item["evidence_id"])]) > 1:
            status = "conflicting_evidence_id"
        elif chunk is None:
            status = "chunk_missing"
        elif any(not item.get(key) or item.get(key) != candidate.get(key)
                 or item.get(key) != chunk.get(key) for key in ("document_id", "document_version")):
            status = "document_version_mismatch"
        elif item.get("section_id") != chunk.get("section_id"):
            status = "section_mismatch"
        else:
            basis = item.get("offset_basis")
            if basis == "chunk":
                status = verify_anchor(item, chunk.get("content", ""))["status"]
                if status == "exact" and any(item.get(key, item[other]) != item[other]
                                               for key, other in (("chunk_start", "start"), ("chunk_end", "end"))):
                    status = "document_chunk_offset_mismatch"
            elif basis == "canonical_document":
                if item.get("source_anchor_kind") == "document_span" and document_snapshot.get("schema_version") == "document_index_v3":
                    status = verify_anchor(item, document_snapshot["source_snapshot"]["canonical_text"])["status"]
                    import json
                    identity = [item["document_id"], item["document_version"], "canonical_document", None, item["start"], item["end"]]
                    expected_id = "ev_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
                    if status == "exact" and item["evidence_id"] != expected_id:
                        status = "source_anchor_id_mismatch"
                    section = document_snapshot["sections_by_id"].get(item["section_id"], {})
                    if status == "exact" and not section.get("start", 0) <= item["start"] < item["end"] <= section.get("end", 0):
                        status = "section_span_mismatch"
                    expected_chunks = {c["chunk_id"] for c in document_snapshot["chunks"] if c["section_id"] == item["section_id"]
                                       and c["document_start"] < item["end"] and c["document_end"] > item["start"]}
                    if status == "exact" and (not expected_chunks or set(item.get("span_chunk_ids") or []) != expected_chunks):
                        status = "document_span_chunks_mismatch"
                    item.update(anchor_valid=status == "exact", anchor_status=status, semantic_status="unknown")
                    (valid if item["anchor_valid"] else rejected).append(item)
                    continue
                block_start = chunk.get("document_start")
                cs, ce = item.get("chunk_start"), item.get("chunk_end")
                if (any(type(x) is not int for x in (block_start, cs, ce, item.get("start"), item.get("end")))
                        or block_start < 0 or item["start"] != block_start + cs
                        or item["end"] != block_start + ce):
                    status = "document_chunk_offset_mismatch"
                else:
                    status = verify_anchor({**item, "start": cs, "end": ce}, chunk.get("content", ""))["status"]
            else:
                status = "unknown_offset_basis"
        item.update(anchor_valid=status == "exact", anchor_status=status, semantic_status="unknown")
        (valid if item["anchor_valid"] else rejected).append(item)
    verified = make_evidence_bundle(valid, bundle.get("searched_levels") or [])
    verified["rejected_items"] = rejected
    verified["anchor_validation_complete"] = True
    verified["evidence_hash"] = hashlib.sha256(canonical_json({
        key: value for key, value in verified.items() if key != "evidence_hash"
    }).encode("utf-8")).hexdigest()
    return verified


def verify_vote_references(vote: Mapping[str, Any], evidence_items: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    result = dict(vote)
    available = {str(item.get("evidence_id")) for item in evidence_items if item.get("evidence_id")}
    referenced = set(result.get("supporting_evidence_ids") or []) | set(result.get("counter_evidence_ids") or [])
    for check in result.get("checks") or []:
        if isinstance(check, Mapping):
            referenced.update(check.get("evidence_ids") or [])
    missing = sorted(reference for reference in referenced if reference not in available)
    if missing:
        result.update({
            "response_status": "invalid_reference", "verdict": None,
            "invalid_evidence_ids": missing,
        })
        reasons = list(result.get("reason_codes") or [])
        if "INVALID_EVIDENCE_REFERENCE" not in reasons:
            reasons.append("INVALID_EVIDENCE_REFERENCE")
        result["reason_codes"] = reasons
        result["error"] = "references not present in this round: " + ", ".join(missing)
    return result
