"""Resolve exactly the proposal selected by a decision; never merge history."""

from .contracts import proposal_digest, canonical_triple, sha256_json


def selected_proposal(repository, candidate, decision):
    audit_id = decision.get("audit_id")
    round_id = decision.get("selected_round_id")
    if not audit_id or not round_id or not decision.get("selected_proposal_digest"):
        raise ValueError("decision has no authoritative selected proposal")
    audit = repository.get_audit(candidate["candidate_id"], audit_id)
    if audit["revision"] != candidate["revision"]:
        raise ValueError("selected proposal revision mismatch")
    proposal = next((p for p in audit["rounds"] if p["round_id"] == round_id), None)
    if not proposal or proposal["proposal_digest"] != decision["selected_proposal_digest"]:
        raise ValueError("selected proposal missing or digest mismatch")
    if proposal_digest(proposal) != proposal["proposal_digest"]:
        raise ValueError("frozen proposal content digest mismatch")
    bundle = proposal["evidence_bundle"]
    if (sha256_json({k: v for k, v in bundle.items() if k != "evidence_hash"}) != bundle["evidence_hash"]
            or sha256_json(proposal["candidate"]) != proposal.get("candidate_review_hash")
            or sha256_json(proposal["ontology_check"]) != proposal.get("ontology_check_hash")
            or sha256_json(repository.get_revision(candidate["candidate_id"], candidate["revision"])) != proposal.get("candidate_review_hash")
            or canonical_triple(proposal["candidate"]) != proposal["canonical_triple"]
            or canonical_triple(candidate) != proposal["canonical_triple"]):
        raise ValueError("selected candidate/evidence content hash mismatch")
    if (proposal["candidate"]["candidate_id"] != candidate["candidate_id"]
            or proposal["candidate"]["revision"] != candidate["revision"]
            or proposal["evidence_bundle"]["evidence_hash"] != decision.get("selected_evidence_hash")):
        raise ValueError("selected candidate/evidence binding mismatch")
    for key in ("ontology_version", "ontology_release_id", "profile_hash", "document_version", "source_index_hash"):
        if proposal.get(key) != candidate.get(key): raise ValueError("selected source/ontology binding mismatch")
    if proposal.get("committee_version") != decision.get("committee_version"):
        raise ValueError("selected committee binding mismatch")
    votes = [v for v in audit["votes"] if v["round_id"] == round_id and v.get("proposal_digest") == proposal["proposal_digest"]]
    from .consensus import aggregate_votes, Committee
    policy = proposal["committee_policy"]
    members = [m["id"] for m in proposal["committee_members"]]
    members.extend("unconfigured_" + str(i + 1) for i in range(policy["committee_size"] - len(members)))
    consensus = aggregate_votes(votes, Committee(members, policy["required_votes"], policy["minimum_distinct_families"], proposal["committee_version"]),
                                proposal=proposal, proposal_digest=proposal["proposal_digest"])
    eligible = set(consensus["eligible_voter_ids"])
    return proposal, [vote for vote in votes if vote["voter_id"] in eligible]
