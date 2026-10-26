"""Source-level candidate v4, preserving incomplete and unmapped semantics."""

from copy import deepcopy
from document.snapshot import json_hash
from schema.relation_registry import RelationRegistry
from schema.entity_registry import local_entity_id
from review_agent.contracts import candidate_id_for

CONTRACT_VERSION = "extraction_candidate_v4"
QUALIFIER_FIELDS = ("polarity", "condition", "time_scope", "applicability", "modality", "quantifier")
STATUS_VALUES = {"extracted", "not_stated", "unknown"}
MODALITIES = {"asserted", "can", "may", "must", "usually", "possible", "necessary", "unknown"}

ITEM_PROPERTIES = {
    "head": {"type": "string", "minLength": 1}, "surface_relation": {"type": "string", "minLength": 1},
    "tail": {"type": "string", "minLength": 1},
    **{key: {"type": ["string", "null"]} for key in ("head_type_candidate", "tail_type_candidate", "relation_id_candidate")},
    "schema_hint": {"type": "string", "enum": ["matched_local_schema", "needs_full_schema_lookup", "unknown"]},
    "tail_kind": {"enum": ["entity", "literal"]}, "value": {"type": ["string", "number", "boolean", "null"]},
    **{key: {"type": ["string", "null"]} for key in ("datatype", "unit", "language")},
    "qualifiers": {"type": "object", "additionalProperties": False,
                   "properties": {key: {"type": ["string", "null"]} for key in QUALIFIER_FIELDS}, "required": list(QUALIFIER_FIELDS)},
    "qualifier_status": {"type": "object", "additionalProperties": False,
                         "properties": {key: {"enum": sorted(STATUS_VALUES)} for key in QUALIFIER_FIELDS}, "required": list(QUALIFIER_FIELDS)},
    "evidence_quotes": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
}
RESPONSE_SCHEMA = {"type": "object", "additionalProperties": False,
                   "properties": {"triplets": {"type": "array", "items": {"type": "object", "properties": ITEM_PROPERTIES,
                       "required": ["head", "surface_relation", "tail", "qualifiers", "qualifier_status", "evidence_quotes"],
                       "additionalProperties": False}},
                       "coverage_status": {"enum": ["complete_for_supplied_scope", "partial", "truncated", "unknown"]}},
                   "required": ["triplets", "coverage_status"]}


def normalize_qualifiers(raw):
    supplied, status = raw.get("qualifiers") or {}, raw.get("qualifier_status") or {}
    if not isinstance(supplied, dict) or not isinstance(status, dict):
        raise ValueError("qualifiers and qualifier_status must be objects")
    values, states, issues = {}, {}, []
    for field in QUALIFIER_FIELDS:
        value = supplied.get(field)
        state = status.get(field, "unknown")
        if value is not None and not isinstance(value, str):
            issues.append("INVALID_QUALIFIER_" + field.upper())
            value, state = None, "unknown"
        if state not in STATUS_VALUES:
            state = "unknown"
        if state == "extracted" and not value or state == "not_stated" and value not in (None, "", "unspecified"):
            issues.append("INCONSISTENT_QUALIFIER_" + field.upper())
            state = "unknown"
        if field == "polarity" and value not in {"positive", "negative"}:
            value, state = "unknown", "unknown"
        elif field == "modality" and value and value not in MODALITIES:
            issues.append("INVALID_MODALITY")
            state = "unknown"
        values[field], states[field] = value, state
    return values, states, issues


def quote_anchors(quotes, scope, index):
    text = index["source_snapshot"]["canonical_text"]
    anchors, issues = [], []
    for quote in quotes:
        if not isinstance(quote, str) or not quote:
            issues.append("EMPTY_EXTRACTION_QUOTE")
            continue
        occurrences, position = [], scope["context_start"]
        while position < scope["context_end"]:
            found = text.find(quote, position, scope["context_end"])
            if found < 0: break
            occurrences.append(found)
            position = found + 1
        if len(occurrences) != 1:
            issues.append("QUOTE_AMBIGUOUS" if occurrences else "QUOTE_UNLOCATED")
            continue
        start, end = occurrences[0], occurrences[0] + len(quote)
        chunks = [chunk for chunk in index["chunks"] if chunk["section_id"] == scope["section_id"]
                  and chunk["document_start"] < end and chunk["document_end"] > start]
        if not chunks:
            issues.append("QUOTE_OUTSIDE_SOURCE_SECTION")
            continue
        owner = next((c for c in chunks if c["document_start"] <= start < c["document_end"]), chunks[0])
        identity = [index["document_id"], index["document_version"], "canonical_document", None, start, end]
        import hashlib, json
        anchors.append({"evidence_id": "ev_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20],
                        "document_id": index["document_id"], "document_version": index["document_version"],
                        "chunk_id": owner["chunk_id"], "section_id": scope["section_id"],
                        "quote": quote, "text": quote, "start": start, "end": end,
                        "chunk_start": start-owner["document_start"], "chunk_end": end-owner["document_start"],
                        "offset_basis": "canonical_document", "source_anchor_kind": "document_span",
                        "span_chunk_ids": [c["chunk_id"] for c in chunks],
                        "anchor_valid": True, "semantic_status": "unknown"})
    return anchors, issues


def normalize_candidate(raw, *, index, scope, ontology, release_binding, run_id, ordinal, prompt_hash):
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) and raw[k].strip()
                                           for k in ("head", "surface_relation", "tail")):
        raise ValueError("candidate requires nonempty head, surface_relation and tail")
    qualifiers, states, issues = normalize_qualifiers(raw)
    if raw.get("tail_kind", "entity") not in {"entity", "literal"}:
        issues.append("INVALID_TAIL_KIND")
    if raw.get("tail_kind") == "literal" and (not raw.get("datatype") or raw.get("value") is None):
        issues.append("INCOMPLETE_TYPED_LITERAL")
    if isinstance(raw.get("value"), (dict, list)):
        issues.append("INVALID_LITERAL_VALUE")
    quotes = raw.get("evidence_quotes") or ([raw["evidence_quote"]] if raw.get("evidence_quote") else [])
    if not isinstance(quotes, list):
        raise ValueError("evidence_quotes must be an array")
    anchors, quote_issues = quote_anchors(quotes, scope, index)
    issues.extend(quote_issues)
    mapping = RelationRegistry(ontology).resolve(raw["surface_relation"])
    relation = RelationRegistry(ontology).properties.get(mapping["relation_id"], {})
    identity = candidate_id_for(run_id, index["document_id"], scope["source_chunk_id"], ordinal)
    triple = {"head": raw["head"], "surface_relation": raw["surface_relation"], "tail": raw["tail"],
              "head_entity_id": local_entity_id(index["document_id"], index["document_version"], raw["head"]),
              "tail_entity_id": local_entity_id(index["document_id"], index["document_version"], raw["tail"]) if raw.get("tail_kind", "entity") == "entity" else None,
              "relation_id": mapping["relation_id"], "relation_label": relation.get("label"),
              "mapping_status": mapping["status"], "head_type": None, "tail_type": None,
              **{key: raw.get(key) for key in ("value", "datatype", "unit", "language")},
              "tail_kind": raw.get("tail_kind", "entity"), "qualifiers": qualifiers, "qualifier_status": states}
    from ontologyBuild.ontology_adapter import class_reference_index, normalize_name
    class_index = class_reference_index(ontology)
    for endpoint in ("head", "tail"):
        triple[endpoint + "_type"] = class_index.get(normalize_name(raw.get(endpoint + "_type_candidate")))
    candidate = {"schema_version": CONTRACT_VERSION, "contract_version": CONTRACT_VERSION,
                 "candidate_id": identity, "revision": 1, "run_id": run_id, "source_ordinal": ordinal,
                 "document_id": index["document_id"], "document_version": index["document_version"],
                 "domain": ontology.get("domain") or "default", "fact_namespace": ontology.get("domain") or "default",
                 "source_snapshot_id": index["source_snapshot"]["snapshot_id"], "source_index_hash": index["index_content_hash"],
                 "source_chunk_id": scope["source_chunk_id"], "section_id": scope["section_id"],
                 "source_scope": deepcopy(scope), "triple": triple, "evidence_items": anchors,
                 "extraction_quote": quotes[0] if quotes else None, "evidence_quotes": quotes,
                 "extraction_hints": {key: raw.get(key) for key in ("relation_id_candidate", "head_type_candidate", "tail_type_candidate", "schema_hint")},
                 "contract_issues": issues, "extraction_prompt_version": "extraction_v4",
                 "extraction_contract_hash": json_hash(RESPONSE_SCHEMA),
                 "extraction_prompt_hash": prompt_hash, "fact_identity_version": "source_local_fact_v1",
                 "fact_key_state": "provisional", **release_binding}
    if not anchors:
        candidate["contract_issues"].append("NO_VERIFIED_SOURCE_ANCHOR")
    if (mapping["relation_id"] and anchors and not issues and all(state != "unknown" for state in states.values())
            and qualifiers["polarity"] in {"positive", "negative"}):
        candidate["fact_key_state"] = "canonical"
    from review_agent.contracts import fact_key_for
    candidate["fact_key"] = fact_key_for(candidate)
    return candidate


def refresh_revision(candidate):
    """A changed expression cannot inherit canonical identity or trusted mapping."""
    if candidate.get("contract_version") != CONTRACT_VERSION:
        return
    candidate["fact_key_state"] = "provisional"
    triple = candidate["triple"]
    for endpoint in ("head", "tail"):
        triple[endpoint + "_entity_id"] = (local_entity_id(candidate["document_id"], candidate["document_version"], triple[endpoint])
                                            if endpoint == "head" or triple.get("tail_kind", "entity") == "entity" else None)
    triple["mapping_status"] = "revision_requires_revalidation"
