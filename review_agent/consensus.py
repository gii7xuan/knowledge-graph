"""Fixed-committee, evidence-aware vote aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from .contracts import ConsensusResult, ResponseStatus, ValidatorVerdict


@dataclass(frozen=True)
class Committee:
    voter_ids: List[str]
    required_votes: int = 3
    minimum_distinct_families: int = 3
    version: str = "committee_v1"

    def __post_init__(self) -> None:
        if len(set(self.voter_ids)) != len(self.voter_ids):
            raise ValueError("committee voter ids must be unique")
        if self.required_votes < 1 or self.required_votes > len(self.voter_ids):
            raise ValueError("required_votes must be within committee size")
        if self.minimum_distinct_families < 1 or self.minimum_distinct_families > len(self.voter_ids):
            raise ValueError("minimum_distinct_families must be within committee size")


def aggregate_votes(
    votes: Iterable[Mapping[str, Any]],
    committee: Committee,
    *,
    proposal_digest: Optional[str] = None,
    unresolved_counterevidence: bool = False,
    unresolved_support_conflict: bool = False,
    proposal: Optional[Mapping[str, Any]] = None,
    admission_blocked: bool = False,
) -> Dict[str, Any]:
    """Count at most one contract-valid vote for every frozen voter slot."""
    if proposal and proposal.get("committee_policy_hash"):
        from .contracts import sha256_json
        policy = {"committee_size": len(committee.voter_ids), "required_votes": committee.required_votes,
                  "minimum_distinct_families": committee.minimum_distinct_families}
        if sha256_json(policy) != proposal["committee_policy_hash"]:
            raise ValueError("current committee thresholds differ from frozen proposal")
    accepted_voters = set(committee.voter_ids)
    unique: Dict[str, Mapping[str, Any]] = {}
    duplicate_attempts = 0
    invalid_attempts = 0
    for vote in votes:
        voter_id = str(vote.get("voter_id") or "")
        if voter_id not in accepted_voters:
            invalid_attempts += 1
            continue
        if voter_id in unique:
            duplicate_attempts += 1
            continue
        if proposal_digest and vote.get("proposal_digest") != proposal_digest:
            invalid_attempts += 1
            continue
        if proposal is not None:
            from .response_contract import CONTRACT_VERSION
            candidate = proposal["candidate"]
            expected = {"task_type": "triple_review", "task_id": candidate["candidate_id"],
                        "source_snapshot_hash": candidate.get("source_snapshot_id"),
                        "object_revision": candidate["revision"], "audit_id": proposal["audit_id"],
                        "round_id": proposal["round_id"], "response_contract_version": CONTRACT_VERSION,
                        "evidence_hash": proposal["evidence_bundle"]["evidence_hash"],
                        **{key: proposal.get(key) for key in ("source_index_hash", "document_version", "ontology_version",
                                "ontology_release_id", "profile_hash", "committee_version", "committee_policy_hash", "prompt_version")}}
            if any(vote.get(key) != value for key, value in expected.items()):
                invalid_attempts += 1
                continue
            member = next((m for m in proposal.get("committee_members", []) if m["id"] == voter_id), None)
            if member and (vote.get("model_id") != member.get("model_id") or vote.get("model_family") != member.get("model_family")
                    or vote.get("model_identity") != str(member.get("model_identity") or member.get("model_id") or "").casefold()):
                invalid_attempts += 1
                continue
            if vote.get("response_status") == "ok":
                from .response_contract import RESPONSE_SCHEMA, validate_response
                from evidence.verification import verify_vote_references
                if (validate_response({k: vote[k] for k in RESPONSE_SCHEMA["properties"] if k in vote})
                        or verify_vote_references(vote, proposal["evidence_bundle"]["items"]).get("response_status") != "ok"):
                    invalid_attempts += 1
                    continue
        unique[voter_id] = vote

    counts = {"valid": 0, "invalid": 0, "uncertain": 0, "error": 0, "missing": 0}
    families: Set[str] = set()
    verdict_families: Dict[str, Set[str]] = {"valid": set(), "invalid": set(), "uncertain": set()}
    material_counter_ids: Set[str] = set()
    material_support_ids: Set[str] = set()
    seen_models: Set[str] = set()
    duplicate_model_votes = 0
    for voter_id in committee.voter_ids:
        vote = unique.get(voter_id)
        if vote is None:
            counts["missing"] += 1
            continue
        if vote.get("response_status") != ResponseStatus.OK.value:
            counts["error"] += 1
            continue
        model_identity = str(vote.get("model_identity") or vote.get("model_id") or "").strip().casefold()
        reported = str(vote.get("returned_model_id") or "").strip().casefold()
        identities = {value for value in (model_identity, reported) if value}
        if identities & seen_models:
            counts["error"] += 1
            duplicate_model_votes += 1
            continue
        seen_models.update(identities)
        verdict = vote.get("verdict")
        if verdict not in {item.value for item in ValidatorVerdict}:
            counts["error"] += 1
            continue
        counts[verdict] += 1
        family = str(vote.get("model_family") or "").strip()
        if family:
            families.add(family)
            verdict_families[verdict].add(family)
        if verdict == ValidatorVerdict.INVALID.value:
            material_counter_ids.update(vote.get("counter_evidence_ids") or [])
        if verdict == ValidatorVerdict.VALID.value:
            material_support_ids.update(vote.get("supporting_evidence_ids") or [])

    # A verified minority conflict is retained even when the caller omitted a flag.
    unresolved_counterevidence = bool(unresolved_counterevidence or (
        counts["valid"] > 0 and material_counter_ids
    ))
    unresolved_support_conflict = bool(unresolved_support_conflict or (
        counts["invalid"] > 0 and material_support_ids and material_counter_ids
    ))
    valid_diversity_ready = len(verdict_families["valid"]) >= committee.minimum_distinct_families
    invalid_diversity_ready = len(verdict_families["invalid"]) >= committee.minimum_distinct_families
    diversity_ready = len(families) >= committee.minimum_distinct_families
    if counts["valid"] >= committee.required_votes and valid_diversity_ready and not unresolved_counterevidence:
        result = ConsensusResult.ACCEPT.value
    elif counts["invalid"] >= committee.required_votes and invalid_diversity_ready and not unresolved_support_conflict:
        result = ConsensusResult.REJECT.value
    else:
        result = ConsensusResult.UNRESOLVED.value

    result_payload = {
        "committee_size": len(committee.voter_ids),
        "required_votes": committee.required_votes,
        "valid_votes": counts["valid"],
        "invalid_votes": counts["invalid"],
        "uncertain_votes": counts["uncertain"],
        "error_votes": counts["error"],
        "missing_votes": counts["missing"],
        "duplicate_attempts": duplicate_attempts,
        "duplicate_model_votes": duplicate_model_votes,
        "invalid_attempts": invalid_attempts,
        "distinct_family_count": len(families),
        "valid_distinct_family_count": len(verdict_families["valid"]),
        "invalid_distinct_family_count": len(verdict_families["invalid"]),
        "diversity_ready": diversity_ready,
        "result": result,
        "unresolved_counterevidence": bool(unresolved_counterevidence),
        "unresolved_support_conflict": bool(unresolved_support_conflict),
        "counter_evidence_ids": sorted(material_counter_ids),
        "supporting_evidence_ids": sorted(material_support_ids),
    }
    if proposal is not None:
        source_votes, contradiction_votes, source_families, contradiction_families = [], [], set(), set()
        triple = proposal["candidate"].get("triple", {})
        expression_complete = (proposal["candidate"].get("contract_version") != "extraction_candidate_v4" or
            (triple.get("qualifiers", {}).get("polarity") in {"positive", "negative"}
             and not any(value == "unknown" for value in triple.get("qualifier_status", {}).values())))
        evidence_ids = {item["evidence_id"] for item in proposal["evidence_bundle"]["items"] if item.get("anchor_valid") is True}
        used_models = set()
        eligible = []
        for voter_id in committee.voter_ids:
            vote = unique.get(voter_id)
            if not vote or vote.get("response_status") != "ok": continue
            identity = str(vote.get("model_identity") or vote.get("model_id") or "").strip().casefold()
            reported = str(vote.get("returned_model_id") or "").strip().casefold()
            identities = {value for value in (identity, reported) if value}
            if not identity or identities & used_models: continue
            used_models.update(identities)
            eligible.append(vote)
            checks = {c["check"]: c for c in vote.get("checks", [])}
            cited = set(vote.get("supporting_evidence_ids") or [])
            source_ready = all(checks.get(name, {}).get("status") in {"pass", "not_applicable"}
                and bool(set(checks.get(name, {}).get("evidence_ids") or []) & evidence_ids)
                for name in ("triple_support", "qualifiers", "relation_direction"))
            source_ready = source_ready and checks.get("counterevidence", {}).get("status") in {"pass", "not_applicable"}
            if vote.get("source_support") == "supported" and source_ready and expression_complete and cited and cited <= evidence_ids:
                source_votes.append(voter_id)
                source_families.add(vote.get("model_family"))
            counter_ids = set(vote.get("counter_evidence_ids") or [])
            if vote.get("source_support") == "contradicted" and counter_ids and counter_ids <= evidence_ids:
                contradiction_votes.append(voter_id)
                contradiction_families.add(vote.get("model_family"))
        source_ready = len(source_votes) >= committee.required_votes and len(source_families-{None, ""}) >= committee.minimum_distinct_families
        contradiction_ready = len(contradiction_votes) >= committee.required_votes and len(contradiction_families-{None, ""}) >= committee.minimum_distinct_families
        current_counters = {eid for vote in eligible for eid in vote.get("counter_evidence_ids", [])}
        source_result = ("supported" if source_ready and not current_counters and not unresolved_counterevidence and not unresolved_support_conflict
                         else "contradicted" if contradiction_ready and not source_votes and not unresolved_support_conflict else "unresolved")
        admission_ready = (source_result == "supported" and counts["valid"] >= committee.required_votes and valid_diversity_ready
                           and proposal.get("ontology_check", {}).get("status") == "pass"
                           and not unresolved_counterevidence and not unresolved_support_conflict and not admission_blocked)
        result_payload.update(source_support_consensus={"result": source_result, "support_votes": len(source_votes),
            "contradiction_votes": len(contradiction_votes), "support_voter_ids": source_votes,
            "committee_size": len(committee.voter_ids), "required_votes": committee.required_votes},
            admission_consensus={"result": "accept_consensus" if admission_ready else "unresolved"},
            eligible_voter_ids=[v["voter_id"] for v in eligible])
        result_payload["result"] = ("accept_consensus" if admission_ready else "reject_consensus" if source_result == "contradicted"
                                    and not unresolved_support_conflict else "unresolved")
    return result_payload


aggregate = aggregate_votes
