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
) -> Dict[str, Any]:
    """Count at most one contract-valid vote for every frozen voter slot."""
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
        model_identity = str(vote.get("model_id") or "").strip().casefold()
        if model_identity and model_identity in seen_models:
            counts["error"] += 1
            duplicate_model_votes += 1
            continue
        if model_identity:
            seen_models.add(model_identity)
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

    return {
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


aggregate = aggregate_votes
