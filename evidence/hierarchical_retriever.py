"""Hierarchical evidence retrieval without pretending relevance is entailment."""

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from evidence.span_locator import find_evidence_span
from evidence.verification import make_evidence_bundle


LEVEL_SCORE = {
    "local_sentence": 0.95,
    "adjacent_sentence": 0.88,
    "current_chunk": 0.78,
    "same_section": 0.68,
    "parent_section": 0.56,
    "other_section": 0.42,
}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _triplet_terms(triplet: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        _as_text(triplet.get("head")),
        _as_text(triplet.get("surface_relation") or triplet.get("relation")),
        _as_text(triplet.get("tail")),
    )


def lexical_relevance_score(sentence: str, triplet: Dict[str, Any]) -> float:
    head, relation, tail = _triplet_terms(triplet)
    if not sentence:
        return 0.0
    score = 0.0
    if head and head in sentence:
        score += 0.4
    if tail and tail in sentence:
        score += 0.4
    if relation and relation in sentence:
        score += 0.2
    return min(score, 1.0)


def _iter_sentence_candidates(
    chunk: Dict[str, Any],
    level: str,
    only_sentence_ids: Optional[set] = None,
) -> Iterable[Dict[str, Any]]:
    for sentence in chunk.get("sentences", []) or []:
        if only_sentence_ids is not None and sentence.get("sentence_id") not in only_sentence_ids:
            continue
        yield {
            "chunk": chunk,
            "sentence": sentence,
            "text": sentence.get("text", ""),
            "start": sentence.get("start"),
            "end": sentence.get("end"),
            "level": level,
        }


def _candidate_to_evidence(
    candidate: Dict[str, Any],
    triplet: Dict[str, Any],
    score: float,
) -> Dict[str, Any]:
    chunk = candidate["chunk"]
    sentence = candidate.get("sentence") or {}
    level = candidate["level"]
    chunk_start = candidate.get("start")
    chunk_end = candidate.get("end")
    document_start = sentence.get("document_start")
    document_end = sentence.get("document_end")
    offset_basis = "canonical_document" if document_start is not None else "chunk"
    start = document_start if document_start is not None else chunk_start
    end = document_end if document_end is not None else chunk_end
    evidence_id = hashlib.sha256(
        f"{chunk.get('document_version')}\n{chunk.get('chunk_id')}\n{start}\n{end}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "evidence_id": f"ev_{evidence_id}",
        "chunk_id": chunk.get("chunk_id"),
        "section_id": chunk.get("section_id"),
        "sentence_id": sentence.get("sentence_id"),
        "text": candidate.get("text", ""),
        "quote": candidate.get("text", ""),
        "start": start,
        "end": end,
        "chunk_start": chunk_start,
        "chunk_end": chunk_end,
        "offset_basis": offset_basis,
        "level": level,
        "retrieval_level": level,
        "retrieval_score": round(max(LEVEL_SCORE.get(level, 0.0), score), 4),
        "lexical_relevance_score": round(score, 4),
        "semantic_status": "unknown",
        "match_status": candidate.get("match_status", "retrieved"),
        "breadcrumb_path": chunk.get("breadcrumb_path"),
        "document_id": chunk.get("document_id"),
        "document_version": chunk.get("document_version", ""),
        "source_role": "source_document",
    }


class HierarchicalEvidenceRetriever:
    """Retrieve evidence from local to broader document scopes."""

    def retrieve(
        self,
        triplet: Dict[str, Any],
        source_chunk: Dict[str, Any],
        document_index: Dict[str, Any],
    ) -> Dict[str, Any]:
        searched_levels: List[str] = []
        located: List[Dict[str, Any]] = []

        quote = _as_text(triplet.get("evidence_quote") or triplet.get("evidence"))
        if quote:
            matched, match_type, start, end = find_evidence_span(quote, source_chunk.get("content", ""))
            if matched:
                searched_levels.append("local_sentence")
                containing_sentence = None
                for sentence in source_chunk.get("sentences", []) or []:
                    if sentence["start"] <= start and end <= sentence["end"]:
                        containing_sentence = sentence
                        break
                sentence = containing_sentence or {
                    "sentence_id": None, "text": source_chunk.get("content", "")[start:end],
                    "start": start, "end": end,
                }
                located.append(_candidate_to_evidence({
                    "chunk": source_chunk, "sentence": sentence, "text": sentence.get("text", quote),
                    "start": sentence.get("start", start), "end": sentence.get("end", end),
                    "level": "local_sentence", "match_status": match_type,
                }, triplet, 1.0))

        candidate_groups = self._candidate_groups(triplet, source_chunk, document_index)
        for level, chunks, sentence_ids in candidate_groups:
            if level not in searched_levels:
                searched_levels.append(level)
            for chunk in chunks:
                for candidate in _iter_sentence_candidates(chunk, level, sentence_ids):
                    score = lexical_relevance_score(candidate["text"], triplet)
                    if score >= 0.8:
                        located.append(_candidate_to_evidence(candidate, triplet, score))

        bundle = make_evidence_bundle(located, searched_levels)
        items = bundle["items"]
        best = None
        if items:
            items.sort(
                key=lambda item: (
                    item.get("retrieval_score", 0.0),
                    -len(item.get("text", "")),
                ),
                reverse=True,
            )
            best = items[0]

        return {
            **bundle,
            "best_evidence": best,
            "support_count": 0,
            "support_items": items,
            "searched_levels": searched_levels,
        }

    def _candidate_groups(
        self,
        triplet: Dict[str, Any],
        source_chunk: Dict[str, Any],
        document_index: Dict[str, Any],
    ) -> List[Tuple[str, List[Dict[str, Any]], Optional[set]]]:
        chunks = document_index.get("chunks", [])
        by_chunk_id = document_index.get("by_chunk_id", {})
        by_section = document_index.get("by_section_id", {})
        groups: List[Tuple[str, List[Dict[str, Any]], Optional[set]]] = []

        source_sentences = source_chunk.get("sentences", []) or []
        local_ids: Set[str] = set()
        # Local means the actual quoted/most relevant sentence, not every sentence in the chunk.
        ranked = sorted(
            source_sentences,
            key=lambda sentence: lexical_relevance_score(sentence.get("text", ""), triplet),
            reverse=True,
        )
        if ranked:
            local_ids.add(ranked[0]["sentence_id"])
        groups.append(("local_sentence", [source_chunk], local_ids))

        adjacent_ids = set()
        for index, sentence in enumerate(source_sentences):
            if sentence["sentence_id"] not in local_ids:
                continue
            if index > 0:
                adjacent_ids.add(source_sentences[index - 1]["sentence_id"])
            if index + 1 < len(source_sentences):
                adjacent_ids.add(source_sentences[index + 1]["sentence_id"])
        groups.append(("adjacent_sentence", [source_chunk], adjacent_ids))
        groups.append(("current_chunk", [source_chunk], None))

        same_section = [
            chunk for chunk in by_section.get(source_chunk.get("section_id"), [])
            if chunk.get("chunk_id") != source_chunk.get("chunk_id")
        ]
        groups.append(("same_section", same_section, None))

        parent_chunks = []
        parent_id = source_chunk.get("parent_section_id")
        if parent_id:
            parent_chunks = [
                chunk for chunk in document_index.get("descendant_chunks_by_section_id", {}).get(parent_id, by_section.get(parent_id, []))
                if chunk.get("chunk_id") != source_chunk.get("chunk_id")
            ]
        groups.append(("parent_section", parent_chunks, None))

        nearby_ids = {
            source_chunk.get("chunk_id"),
            source_chunk.get("prev_chunk_id"),
            source_chunk.get("next_chunk_id"),
        }
        nearby = [by_chunk_id[item] for item in nearby_ids if item and item in by_chunk_id]
        groups.append(("other_section", [chunk for chunk in nearby if chunk], None))
        if not nearby:
            groups.append(("other_section", chunks, None))
        return groups


def retrieve_initial(candidate: Dict[str, Any], document_snapshot: Dict[str, Any], *, max_items: int = 6) -> Dict[str, Any]:
    triple = candidate.get("triple") if isinstance(candidate.get("triple"), dict) else candidate
    source_id = candidate.get("source_chunk_id") or candidate.get("source_id")
    source = document_snapshot.get("by_chunk_id", {}).get(source_id)
    if source is None:
        return make_evidence_bundle([], ["source_chunk_missing"])
    result = HierarchicalEvidenceRetriever().retrieve(triple, source, document_snapshot)
    limited = make_evidence_bundle(
        result.get("items", [])[:max(0, int(max_items))], result.get("searched_levels", []),
    )
    limited["best_evidence"] = limited["items"][0] if limited["items"] else None
    limited["support_count"] = 0
    limited["support_items"] = limited["items"]
    return limited


def retrieve_more(
    candidate: Dict[str, Any], document_snapshot: Dict[str, Any], request: Dict[str, Any],
    seen_evidence_ids: Iterable[str],
) -> Dict[str, Any]:
    allowed_scopes = {"current_chunk", "same_section", "parent_section", "other_section"}
    scope = str(request.get("scope") or "same_section")
    if scope not in allowed_scopes:
        raise ValueError("unsupported evidence scope")
    limit = min(6, max(1, int(request.get("limit") or 4)))
    bundle = retrieve_initial(candidate, document_snapshot, max_items=12)
    seen = set(seen_evidence_ids)
    items = [item for item in bundle.get("items", []) if item.get("evidence_id") not in seen]
    scoped = [item for item in items if item.get("retrieval_level") == scope]
    if scoped:
        items = scoped
    return make_evidence_bundle(items[:limit], [scope])
