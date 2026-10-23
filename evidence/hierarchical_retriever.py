"""Evidence retrieval against frozen source chunks; relevance is never entailment."""

import hashlib
from typing import Any, Dict, List, Optional

from evidence.span_locator import find_evidence_span
from evidence.verification import make_evidence_bundle

LEVEL_SCORE = {"local_sentence": .95, "adjacent_sentence": .88, "current_chunk": .78,
               "same_section": .68, "parent_section": .56, "other_section": .42}


def lexical_relevance_score(sentence: str, triplet: Dict[str, Any]) -> float:
    fields = (("head", .4), ("tail", .4), ("surface_relation", .2))
    return min(1., sum(weight for field, weight in fields
                       if str(triplet.get(field) or (triplet.get("relation") if field == "surface_relation" else "") or "").strip()
                       and str(triplet.get(field) or triplet.get("relation") or "").strip() in sentence))


def _index(snapshot):
    chunks = snapshot.get("chunks") or []
    return chunks, snapshot.get("by_chunk_id") or {c["chunk_id"]: c for c in chunks}


def _evidence(chunk, start, end, level, *, sentence_id=None, match_status="retrieved", score=0.):
    document_start = chunk.get("document_start")
    canonical = type(document_start) is int and document_start >= 0
    basis = "canonical_document" if canonical else "chunk"
    absolute_start = document_start + start if canonical else start
    absolute_end = document_start + end if canonical else end
    identity = [chunk.get("document_id"), chunk.get("document_version"), basis,
                None if canonical else chunk["chunk_id"], absolute_start, absolute_end]
    import json
    eid = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    quote = chunk["content"][start:end]
    title = str(chunk.get("breadcrumb_path") or "")
    content_type = "table_of_contents" if "目录" in title else (
        "heading" if quote.strip().startswith("#") else "body")
    return {
        "evidence_id": "ev_" + eid, "document_id": chunk.get("document_id"),
        "document_version": chunk.get("document_version"), "chunk_id": chunk["chunk_id"],
        "section_id": chunk.get("section_id"), "sentence_id": sentence_id,
        "quote": quote, "text": quote, "start": absolute_start, "end": absolute_end,
        "chunk_start": start, "chunk_end": end, "offset_basis": basis,
        "breadcrumb_path": title, "retrieval_level": level, "level": level,
        "retrieved_via_levels": [level], "retrieval_score": max(LEVEL_SCORE[level], score),
        "lexical_relevance_score": score, "match_status": match_status,
        "semantic_status": "unknown", "source_role": "source_document", "content_type": content_type,
    }


def _sentence_evidence(chunk, sentence, level, score=0.):
    return _evidence(chunk, sentence["start"], sentence["end"], level,
                     sentence_id=sentence.get("sentence_id"), score=score)


def _rank(items, query=""):
    terms = [term for term in str(query).split() if len(term) > 1]
    return sorted(items, key=lambda item: (
        sum(term in item["quote"] for term in terms), item.get("lexical_relevance_score", 0.),
        item.get("retrieval_score", 0.)), reverse=True)


def _limit(items, max_items, max_chars=None):
    selected, used = [], 0
    for item in items:
        size = len(item["quote"])
        if len(selected) >= max_items:
            break
        if max_chars is not None and used + size > max_chars:
            continue
        selected.append(item)
        used += size
    return selected


def retrieve_initial(candidate: Dict[str, Any], document_snapshot: Dict[str, Any], *, max_items: int = 6) -> Dict[str, Any]:
    triple = candidate.get("triple") if isinstance(candidate.get("triple"), dict) else candidate
    chunks, by_id = _index(document_snapshot)
    source = by_id.get(candidate.get("source_chunk_id") or candidate.get("source_id"))
    quote = str(candidate.get("extraction_quote") or candidate.get("evidence_quote")
                or triple.get("evidence_quote") or triple.get("evidence") or candidate.get("evidence") or "").strip()
    # Historical items are localization hints only. Rebuild every ID/span from frozen text.
    hints = [by_id.get(item.get("chunk_id")) for item in candidate.get("evidence_items") or [] if isinstance(item, dict)]
    ordered = []
    for chunk in [source, *hints, *chunks]:
        if chunk is not None and chunk["chunk_id"] not in {x["chunk_id"] for x in ordered}:
            if chunk.get("document_id") == candidate.get("document_id", chunk.get("document_id")) and chunk.get("document_version") == candidate.get("document_version", chunk.get("document_version")):
                ordered.append(chunk)
    selected, levels = [], []
    anchor_chunk = source
    if quote:
        for chunk in ordered:
            found, match, start, end = find_evidence_span(quote, chunk.get("content", ""))
            if not found:
                continue
            anchor_chunk = chunk
            covering = [s for s in chunk.get("sentences") or [] if s["start"] < end and s["end"] > start]
            sentence_id = covering[0].get("sentence_id") if len(covering) == 1 and covering[0]["start"] == start and covering[0]["end"] == end else None
            selected.append(_evidence(chunk, start, end, "local_sentence", sentence_id=sentence_id, match_status=match, score=1.))
            # Reserve space for adjacent qualifiers even if they contain neither entity.
            sentences = chunk.get("sentences") or []
            indices = [i for i, s in enumerate(sentences) if s in covering]
            if indices:
                for i in (indices[0] - 1, indices[-1] + 1):
                    if 0 <= i < len(sentences):
                        selected.append(_sentence_evidence(chunk, sentences[i], "adjacent_sentence"))
                selected.extend(_sentence_evidence(chunk, s, "local_sentence") for s in covering)
            levels.extend(["local_sentence", "adjacent_sentence"])
            break
    if anchor_chunk:
        local = []
        for sentence in anchor_chunk.get("sentences") or []:
            score = lexical_relevance_score(sentence.get("text", ""), triple)
            if score > 0:
                local.append(_sentence_evidence(anchor_chunk, sentence, "current_chunk", score))
        selected.extend(_rank(local))
        levels.append("current_chunk")
    bundle = make_evidence_bundle(selected, levels or ["source_quote_unlocated"])
    return make_evidence_bundle(_limit(bundle["items"], max(0, int(max_items))), bundle["searched_levels"])


def retrieve_more(candidate: Dict[str, Any], document_snapshot: Dict[str, Any], request: Dict[str, Any],
                  seen_evidence_ids, *, max_chars: Optional[int] = None) -> Dict[str, Any]:
    scope = str(request.get("scope") or "same_section")
    if scope not in {"current_chunk", "same_section", "parent_section", "other_section"}:
        raise ValueError("unsupported evidence scope")
    chunks, by_id = _index(document_snapshot)
    source = by_id.get(candidate.get("source_chunk_id") or candidate.get("source_id"))
    if source is None:
        return make_evidence_bundle([], [scope])
    section, parent = source.get("section_id"), source.get("parent_section_id")
    if scope == "current_chunk":
        targets = [source]
    elif scope == "same_section":
        targets = [c for c in chunks if c.get("section_id") == section and c["chunk_id"] != source["chunk_id"]]
    elif scope == "parent_section":
        targets = [c for c in chunks if parent and (c.get("section_id") == parent or
                   c.get("parent_section_id") == parent or
                   any(n.get("section_id") == parent for n in c.get("section_ancestry") or []))]
    else:
        targets = [c for c in chunks if c.get("section_id") != section]
    triple = candidate.get("triple") if isinstance(candidate.get("triple"), dict) else candidate
    seen, found = set(seen_evidence_ids), []
    for chunk in targets:
        for sentence in chunk.get("sentences") or []:
            score = lexical_relevance_score(sentence.get("text", ""), triple)
            query_match = any(term in sentence.get("text", "") for term in str(request.get("query") or "").split() if len(term) > 1)
            if scope == "current_chunk" or score > 0 or query_match:
                item = _sentence_evidence(chunk, sentence, scope, score)
                if item["evidence_id"] not in seen:
                    found.append(item)
    unique = make_evidence_bundle(found, [scope])
    return make_evidence_bundle(_limit(_rank(unique["items"], request.get("query")),
                                       min(6, max(1, int(request.get("limit") or 4))), max_chars), [scope])


class HierarchicalEvidenceRetriever:
    def retrieve(self, triplet, source_chunk, document_index):
        candidate = {**triplet, "source_chunk_id": source_chunk["chunk_id"],
                     "document_id": source_chunk.get("document_id"),
                     "document_version": source_chunk.get("document_version")}
        bundle = retrieve_initial(candidate, document_index, max_items=100)
        # Preserve broad lookup for the extraction-stage compatibility API.
        extra = list(bundle["items"])
        levels = list(bundle["searched_levels"])
        for scope in ("same_section", "parent_section", "other_section"):
            found = retrieve_more(candidate, document_index, {"scope": scope, "limit": 6}, [])
            extra.extend(item for item in found["items"] if item.get("lexical_relevance_score", 0.) >= .8)
            levels.extend(found["searched_levels"])
        bundle = make_evidence_bundle(extra, levels)
        bundle.update(best_evidence=bundle["items"][0] if bundle["items"] else None,
                      support_count=0, support_items=bundle["items"])
        return bundle
