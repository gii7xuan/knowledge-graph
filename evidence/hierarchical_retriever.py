"""Hierarchical evidence retrieval for candidate triples."""

from typing import Any, Dict, Iterable, List, Optional, Tuple

from evidence.span_locator import find_evidence_span


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


def _sentence_support_score(sentence: str, triplet: Dict[str, Any]) -> float:
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
    entailment = max(score, 0.3 if score > 0 else 0.0)
    return {
        "evidence_id": f"ev_{chunk.get('chunk_id')}_{sentence.get('sentence_id', 'chunk')}",
        "chunk_id": chunk.get("chunk_id"),
        "sentence_id": sentence.get("sentence_id"),
        "text": candidate.get("text", ""),
        "start": candidate.get("start"),
        "end": candidate.get("end"),
        "level": level,
        "retrieval_score": round(max(LEVEL_SCORE.get(level, 0.0), score), 4),
        "entailment_score": round(entailment, 4),
        "contradiction_score": round(max(0.0, 1.0 - entailment), 4),
        "breadcrumb_path": chunk.get("breadcrumb_path"),
        "document_id": chunk.get("document_id"),
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
        support: List[Dict[str, Any]] = []

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
                support.append(
                    {
                        "evidence_id": f"ev_{source_chunk.get('chunk_id')}_{start}_{end}",
                        "chunk_id": source_chunk.get("chunk_id"),
                        "sentence_id": containing_sentence.get("sentence_id") if containing_sentence else None,
                        "text": quote,
                        "start": start,
                        "end": end,
                        "level": "local_sentence",
                        "match_type": match_type,
                        "retrieval_score": 1.0,
                        "entailment_score": 1.0,
                        "contradiction_score": 0.0,
                        "breadcrumb_path": source_chunk.get("breadcrumb_path"),
                        "document_id": source_chunk.get("document_id"),
                    }
                )

        candidate_groups = self._candidate_groups(source_chunk, document_index)
        for level, chunks, sentence_ids in candidate_groups:
            if level not in searched_levels:
                searched_levels.append(level)
            for chunk in chunks:
                for candidate in _iter_sentence_candidates(chunk, level, sentence_ids):
                    score = _sentence_support_score(candidate["text"], triplet)
                    if score >= 0.8:
                        support.append(_candidate_to_evidence(candidate, triplet, score))

        best = None
        if support:
            support.sort(
                key=lambda item: (
                    item.get("entailment_score", 0.0),
                    item.get("retrieval_score", 0.0),
                    -len(item.get("text", "")),
                ),
                reverse=True,
            )
            best = support[0]

        return {
            "best_evidence": best,
            "support_count": len(support),
            "support_items": support,
            "searched_levels": searched_levels,
        }

    def _candidate_groups(
        self,
        source_chunk: Dict[str, Any],
        document_index: Dict[str, Any],
    ) -> List[Tuple[str, List[Dict[str, Any]], Optional[set]]]:
        chunks = document_index.get("chunks", [])
        by_chunk_id = document_index.get("by_chunk_id", {})
        by_section = document_index.get("by_section_id", {})
        groups: List[Tuple[str, List[Dict[str, Any]], Optional[set]]] = []

        source_sentences = source_chunk.get("sentences", []) or []
        local_ids = {sentence["sentence_id"] for sentence in source_sentences}
        groups.append(("local_sentence", [source_chunk], local_ids))

        adjacent_ids = set()
        for index, sentence in enumerate(source_sentences):
            adjacent_ids.add(sentence["sentence_id"])
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
                chunk for chunk in by_section.get(parent_id, [])
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
