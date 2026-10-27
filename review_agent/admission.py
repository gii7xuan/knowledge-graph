"""One fail-closed admission contract for automatic, human and import paths."""
from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any
import math

from document.hierarchy_index import load_document_index
from document.index_integrity import validate_document_index
from document.snapshot import validate_snapshot
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontologyBuild.release_service import load_release
from schema.relation_registry import RelationRegistry
from schema.entity_registry import local_entity_id
from .contracts import canonical_json, sha256_json, fact_key_for
from .ontology_guard import OntologyGuard, load_snapshot

QUALIFIERS = ("polarity", "condition", "time_scope", "applicability", "modality", "quantifier")
ROOT = Path(__file__).resolve().parents[1]
VERSION = "admission_v1"


@dataclass
class AdmissionResult:
    allowed: bool
    reason_codes: list = field(default_factory=list)
    evidence_bundle: dict = field(default_factory=dict)
    assessments: list = field(default_factory=list)
    guard: dict = field(default_factory=dict)
    canonical_fact_key: str | None = None

    def to_dict(self):
        return {"version": VERSION, "allowed": self.allowed, "reason_codes": self.reason_codes,
                "evidence_hash": self.evidence_bundle.get("evidence_hash"), "canonical_fact_key": self.canonical_fact_key,
                "guard": self.guard}


def validate_admission(candidate, decision, ontology_release, source_snapshot, evidence_bundle, assessments, mode):
    """Only source/semantic facts enter this result; flags and cached booleans do not."""
    reasons = []
    if candidate.get("contract_version") != "extraction_candidate_v4": reasons.append("LEGACY_CANDIDATE_REQUIRES_VERIFIED_V4_REVISION")
    if candidate.get("fact_key_state") != "canonical": reasons.append("CANDIDATE_IDENTITY_PROVISIONAL")
    if decision.get("revision") != candidate.get("revision"): reasons.append("DECISION_REVISION_MISMATCH")
    if decision.get("candidate_id") != candidate.get("candidate_id"): reasons.append("DECISION_CANDIDATE_MISMATCH")
    if decision.get("active") is False: reasons.append("DECISION_NOT_CURRENT")
    for key, release_key in (("ontology_release_id", "release_id"), ("ontology_version", "ontology_version"), ("profile_hash", "profile_hash")):
        if not candidate.get(key) or candidate.get(key) != ontology_release.manifest.get(release_key): reasons.append("ONTOLOGY_RELEASE_BINDING_MISMATCH")
    if candidate.get("workflow_state") in {"awaiting_ontology_review", "awaiting_ontology_release", "schema_unresolved", "reaudit_required", "revision_required"}:
        reasons.append("CANDIDATE_WORKFLOW_NOT_ADMITTED")
    triple = candidate.get("triple") or {}
    qualifiers, statuses = triple.get("qualifiers") or {}, triple.get("qualifier_status") or {}
    if (not isinstance(qualifiers, dict) or not isinstance(statuses, dict)
            or any(not isinstance(triple.get(k), str) for k in ("head", "tail", "relation_id", "tail_kind"))
            or any(triple.get(k) is not None and not isinstance(triple[k], str) for k in ("head_type", "tail_type", "datatype", "unit", "language"))
            or any(v is not None and not isinstance(v, str) for v in qualifiers.values())
            or any(not isinstance(v, str) for v in statuses.values())
            or not isinstance(assessments, list) or any(not isinstance(a, dict) for a in assessments)):
        return AdmissionResult(False, sorted(set(reasons + ["CANDIDATE_OR_ASSESSMENT_FIELDS_INVALID"])))
    if set(qualifiers) != set(QUALIFIERS) or set(statuses) != set(QUALIFIERS): reasons.append("COMPLETE_QUALIFIERS_REQUIRED")
    if qualifiers.get("polarity") not in {"positive", "negative"}: reasons.append("POLARITY_UNKNOWN")
    for key in QUALIFIERS:
        if statuses.get(key) not in {"extracted", "not_stated"}: reasons.append("QUALIFIER_STATUS_UNKNOWN")
        if qualifiers.get(key) is not None and not isinstance(qualifiers[key], str): reasons.append("QUALIFIER_VALUE_TYPE_INVALID")
        if statuses.get(key) == "extracted" and (not isinstance(qualifiers.get(key), str) or not qualifiers[key].strip()): reasons.append("EXTRACTED_QUALIFIER_VALUE_MISSING")
        if statuses.get(key) == "not_stated" and qualifiers.get(key) is not None: reasons.append("QUALIFIER_VALUE_STATUS_CONFLICT")
    from extract_triplets.candidate_contract import MODALITIES
    if qualifiers.get("modality") is not None and qualifiers["modality"] not in MODALITIES - {"unknown"}: reasons.append("MODALITY_UNKNOWN_OR_UNSUPPORTED")
    for endpoint in ("head", "tail"):
        if endpoint == "tail" and triple.get("tail_kind") == "literal": continue
        # Present implementation uses conservative document-local identity. It must
        # never silently turn equal display names into cross-document entities.
        expected = local_entity_id(candidate.get("document_id"), candidate.get("document_version"), triple.get(endpoint))
        if not triple.get(endpoint) or triple.get(endpoint + "_entity_id") != expected: reasons.append("ENTITY_IDENTITY_UNVERIFIED")
    relation = RelationRegistry(ontology_release.ontology).resolve(triple.get("relation_id") or "")
    if not relation["relation_id"] or relation["relation_id"] != triple.get("relation_id"): reasons.append("STANDARD_RELATION_ID_REQUIRED")
    from schema.fact_identity import relation_semantic_hash
    if candidate.get("fact_identity_version") != "fact_identity_v2" or triple.get("relation_semantic_hash") != relation_semantic_hash(ontology_release.ontology, triple.get("relation_id")):
        reasons.append("RELATION_SEMANTIC_IDENTITY_UNVERIFIED")
    if triple.get("tail_kind") not in {"entity", "literal"}: reasons.append("TAIL_KIND_UNSUPPORTED")
    from ontologyBuild.ontology_adapter import iter_classes
    formal_classes = {c["id"] for c in iter_classes(ontology_release.ontology)}
    if triple.get("head_type") not in formal_classes or (triple.get("tail_kind") == "entity" and triple.get("tail_type") not in formal_classes):
        reasons.append("STANDARD_ENDPOINT_TYPE_REQUIRED")
    if triple.get("tail_kind") == "literal":
        if not triple.get("datatype") or triple.get("value") is None or isinstance(triple.get("value"), (dict, list)):
            reasons.append("TYPED_LITERAL_INCOMPLETE")
    guard = OntologyGuard(load_snapshot(ontology_release.ontology)).check(candidate)
    if guard["status"] != "pass": reasons.append("ONTOLOGY_GUARD_NOT_PASS")
    try:
        validate_snapshot(source_snapshot["source_snapshot"])
        validate_document_index(source_snapshot, candidate.get("source_index_hash"))
        if (candidate.get("document_id") != source_snapshot["document_id"] or candidate.get("document_version") != source_snapshot["document_version"]
                or candidate.get("source_snapshot_id") != source_snapshot["source_snapshot"]["snapshot_id"]):
            reasons.append("SOURCE_SNAPSHOT_BINDING_MISMATCH")
        if sha256_json({k: v for k, v in evidence_bundle.items() if k != "evidence_hash"}) != evidence_bundle.get("evidence_hash"):
            reasons.append("EVIDENCE_BUNDLE_HASH_MISMATCH")
        verified = verify_evidence_bundle(evidence_bundle, source_snapshot, candidate)
        if not verified["items"] or verified.get("rejected_items"): reasons.append("SOURCE_ANCHOR_INVALID_OR_MISSING")
    except (KeyError, ValueError, TypeError) as exc:
        reasons.append("SOURCE_SNAPSHOT_OR_INDEX_INVALID")
        verified = {}
    ids = {i["evidence_id"] for i in verified.get("items", [])}
    if not assessments or any(not isinstance(a, dict) or a.get("evidence_id") not in ids for a in assessments): reasons.append("ASSESSMENT_REFERENCES_INVALID")
    if any(a.get("role") not in {"support", "counter", "context", "irrelevant"} for a in assessments): reasons.append("ASSESSMENT_ROLE_INVALID")
    supports = {a["evidence_id"] for a in assessments if a.get("role") == "support"}
    if not supports: reasons.append("SOURCE_SEMANTIC_SUPPORT_MISSING")
    if any(a.get("role") == "counter" for a in assessments): reasons.append("UNRESOLVED_COUNTEREVIDENCE")
    final = decision.get("decision")
    if final == "auto_accept":
        policy = decision.get("admission_policy") or {}
        threshold = policy.get("accept_threshold")
        probability = decision.get("calibrated_probability")
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in (threshold, probability)):
            reasons.append("AUTOMATIC_PROBABILITY_OR_THRESHOLD_INVALID")
            threshold, probability = 1, -1
        if mode != "active" or not policy.get("auto_accept_enabled"): reasons.append("AUTOMATIC_ADMISSION_DISABLED")
        if (not policy.get("policy_ready") or not policy.get("calibrator_ready") or threshold is None
                or decision.get("calibration_status") != "ok" or decision.get("calibrated_probability") is None
                or probability < (threshold or 0)
                or decision.get("policy_version") != policy.get("policy_version")):
            reasons.append("AUTOMATIC_POLICY_OR_CALIBRATION_INVALID")
        if decision.get("consensus", {}).get("admission_consensus", {}).get("result") != "accept_consensus": reasons.append("CURRENT_ADMISSION_CONSENSUS_MISSING")
        if not decision.get("selected_proposal_digest") or not decision.get("selected_round_id"): reasons.append("EXACT_PROPOSAL_REQUIRED")
    elif final == "human_accept":
        if decision.get("source") != "human_confirmed" or not decision.get("human_bundle_id"):
            reasons.append("BOUND_HUMAN_BUNDLE_REQUIRED")
    else: reasons.append("DECISION_DOES_NOT_ADMIT")
    try: fact_key = fact_key_for(candidate)
    except (ValueError, TypeError, OverflowError): fact_key = "provisional:invalid_literal"
    if not candidate.get("fact_key") or candidate["fact_key"] != fact_key or fact_key.startswith("provisional:"):
        reasons.append("CANONICAL_FACT_KEY_MISMATCH")
    return AdmissionResult(not reasons, sorted(set(reasons)), verified, assessments, guard, fact_key)


def load_admission_artifacts(repository, candidate):
    manifest = repository.get_run(candidate["run_id"])
    path = candidate.get("ontology_path") or manifest.get("ontology_path")
    if not path: raise ValueError("ontology release path missing")
    path = Path(path)
    if not path.is_absolute(): path = ROOT / path
    release = load_release(path.parent if path.is_file() else path)
    document = load_document_index(manifest["document_path"], candidate.get("source_index_hash"), require_verified=True)
    return release, document


def canonicalize_revision(repository, candidate):
    """Compute identity before freezing a new revision; this grants no admission."""
    if candidate.get("contract_version") != "extraction_candidate_v4": return candidate
    candidate = deepcopy(candidate)
    try:
        release, document = load_admission_artifacts(repository, candidate)
        triple = candidate["triple"]
        registry = RelationRegistry(release.ontology)
        resolved = registry.resolve(triple.get("relation_id") or "")
        if not resolved["relation_id"]: resolved = registry.resolve(triple.get("surface_relation") or "")
        triple.update(relation_id=resolved["relation_id"], mapping_status=resolved["status"])
        from schema.fact_identity import relation_semantic_hash
        candidate["fact_identity_version"] = "fact_identity_v2"
        triple["relation_semantic_hash"] = relation_semantic_hash(release.ontology, resolved["relation_id"])
        guard = OntologyGuard(load_snapshot(release.ontology))
        from ontologyBuild.ontology_adapter import normalize_name
        for field in ("head_type", "tail_type"):
            if triple.get(field): triple[field] = guard.class_index.get(normalize_name(triple[field]))
        for endpoint in ("head", "tail"):
            triple[endpoint + "_entity_id"] = local_entity_id(candidate["document_id"], candidate["document_version"], triple[endpoint]) if endpoint == "head" or triple.get("tail_kind") == "entity" else None
        statuses = triple.get("qualifier_status") or {}
        complete = set(statuses) == set(QUALIFIERS) and all(v in {"extracted", "not_stated"} for v in statuses.values())
        if resolved["relation_id"] and complete and (triple.get("qualifiers") or {}).get("polarity") in {"positive", "negative"}:
            candidate["fact_key_state"] = "canonical"
        candidate["fact_key"] = fact_key_for(candidate)
    except (ValueError, KeyError, OSError):
        candidate["fact_key_state"] = "provisional"
        candidate["fact_key"] = fact_key_for(candidate)
    return candidate
