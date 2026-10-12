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
        return (document_id, document_version, basis, int(item["start"]), int(item["end"]))
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
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return {"valid": False, "status": "invalid_offset", "actual_quote": ""}
    if start < 0 or end < start or end > len(document_or_chunk):
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
    return result
