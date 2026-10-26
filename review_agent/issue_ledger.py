"""Server-owned issue lifecycle; planner prose cannot resolve an obligation."""

from .contracts import sha256_json
import copy

REVISION_ISSUES = {"CONDITION_MISSING", "QUALIFIER_MISMATCH", "DIRECTION_MISMATCH", "revision_proposed"}
SOURCE_ISSUES = {"EVIDENCE_INSUFFICIENT", "source_conflict", "counterevidence", "unverified_source"}


class IssueLedger:
    def __init__(self, state):
        self.state = state

    def inherit(self, repository):
        """A new audit cannot erase revision-bound obligations or their evidence."""
        from .response_contract import CONTRACT_VERSION
        prior = repository.previous_revision_audit(self.state.candidate["candidate_id"], self.state.candidate["revision"], self.state.audit_id)
        if not prior or prior["context"].get("response_contract_version") != CONTRACT_VERSION:
            return
        saved = (prior.get("agent_state") or {}).get("state", {})
        carried = [i for i in prior["context"].get("issue_history", saved.get("issues", []))
                   if i.get("status", "open") != "resolved" and i["code"] in REVISION_ISSUES | SOURCE_ISSUES | {"ontology_gap"}]
        for issue in copy.deepcopy(carried):
            issue["inherited_from_audit"] = prior["audit_id"]
            self.state.issues.append(issue)
            self.event(issue, "inherited", source_audit_id=prior["audit_id"])
        if carried:
            archive = saved.get("archive") or ((prior.get("rounds") or [{}])[-1].get("evidence_bundle") or {}).get("items", [])
            self.state.archive = copy.deepcopy(archive)
            self.state.protected_ids = sorted(set(saved.get("protected_ids", [])) |
                {eid for issue in carried for eid in issue.get("evidence_ids", [])})

    def event(self, issue, action, proposal=None, **details):
        event = {"issue_id": issue["issue_id"], "action": action, "proposal_digest": proposal,
                 "candidate_id": self.state.candidate["candidate_id"], "revision": self.state.candidate["revision"],
                 **details, "sequence": len(self.state.issue_events)}
        event["event_id"] = sha256_json([self.state.audit_id, event])
        self.state.issue_events.append(event)

    def open(self, code, description, critical=False, *, proposal=None, evidence_ids=(), evidence_hash=None):
        existing = next((i for i in self.state.issues if i["code"] == code and i.get("status", "open") != "resolved"), None)
        if existing:
            existing["critical"] = bool(existing.get("critical") or critical)
            existing["evidence_ids"] = sorted(set(existing.get("evidence_ids", [])) | set(evidence_ids))
            return existing
        issue = {"issue_id": "issue_" + sha256_json([self.state.audit_id, code, len(self.state.issues)])[:24],
                 "code": code, "description": description, "status": "open", "critical": critical,
                 "opened_in_proposal": proposal, "opened_evidence_hash": evidence_hash,
                 "evidence_ids": sorted(set(evidence_ids)), "resolution_evidence_ids": [],
                 "resolved_in_proposal": None, "resolution_rule": None,
                 "requires_revision": code in REVISION_ISSUES}
        self.state.issues.append(issue)
        self.event(issue, "opened", proposal)
        return issue

    def resolve(self, issue, proposal, rule, evidence_ids):
        issue.update(status="resolved", resolved_in_proposal=proposal["proposal_digest"],
                     resolution_rule=rule, resolution_evidence_ids=sorted(set(evidence_ids)))
        self.event(issue, "resolved", proposal["proposal_digest"], rule=rule, evidence_ids=issue["resolution_evidence_ids"])

    def observe(self, proposal, votes, consensus, required_votes):
        eligible = set(consensus.get("eligible_voter_ids", []))
        valid = [v for v in votes if v.get("voter_id") in eligible]
        digest, evidence_hash = proposal["proposal_digest"], proposal["evidence_bundle"]["evidence_hash"]
        counter_ids = {eid for vote in valid for eid in vote.get("counter_evidence_ids", [])}
        source = consensus.get("source_support_consensus", {}).get("result")
        if counter_ids and (any(v.get("source_support") == "supported" for v in valid)
                            or source != "contradicted"):
            self.open("source_conflict", "存在需要独立复核适用范围的反证", True, proposal=digest,
                      evidence_ids=counter_ids | {eid for v in valid for eid in v.get("supporting_evidence_ids", [])}, evidence_hash=evidence_hash)
        for vote in valid:
            for code in set(vote.get("reason_codes", [])) & (REVISION_ISSUES | {"EVIDENCE_INSUFFICIENT"}):
                self.open(code, "当前候选存在来源、限定或方向义务", True, proposal=digest,
                          evidence_ids=vote.get("counter_evidence_ids") or vote.get("supporting_evidence_ids") or [], evidence_hash=evidence_hash)
            if vote.get("source_support") == "insufficient":
                self.open("EVIDENCE_INSUFFICIENT", "当前原文不足，需定向补证", True, proposal=digest,
                          evidence_ids=[item["evidence_id"] for item in proposal["evidence_bundle"]["items"]], evidence_hash=evidence_hash)
        if source in {"supported", "contradicted"}:
            supporting = ([v for v in valid if v["voter_id"] in consensus["source_support_consensus"]["support_voter_ids"]]
                          if source == "supported" else [v for v in valid if v.get("source_support") == "contradicted"])
            source_ids = {eid for vote in supporting for eid in [*vote.get("supporting_evidence_ids", []), *vote.get("counter_evidence_ids", [])]}
            for issue in self.state.issues:
                if issue.get("status", "open") == "resolved" or issue.get("requires_revision"): continue
                if (issue["code"] == "EVIDENCE_INSUFFICIENT" and issue.get("opened_evidence_hash") != evidence_hash
                        and {i["evidence_id"] for i in proposal["evidence_bundle"]["items"]} - set(issue.get("evidence_ids", []))):
                    self.resolve(issue, proposal, "new_evidence_independent_source_recheck", source_ids)
                if issue["code"] in {"source_conflict", "counterevidence"}:
                    protected = set(issue.get("evidence_ids", []))
                    reviews = [vote for vote in supporting if any(c["check"] == "counterevidence"
                        and c["status"] in {"pass", "not_applicable"} and protected <= set(c["evidence_ids"])
                        for c in vote.get("checks", []))]
                    if protected and len(reviews) >= required_votes:
                        self.resolve(issue, proposal, "independent_current_counterevidence_recheck", protected | source_ids)
        # Relevance exclusions require independent quorum and retain their history.
        current_material = counter_ids | {eid for v in valid for eid in v.get("supporting_evidence_ids", [])}
        for eid in set(self.state.evidence_exclusions) & current_material:
            prior = self.state.evidence_exclusions.pop(eid)
            self.event({"issue_id": "relevance_" + eid}, "exclusion_revoked", digest, evidence_id=eid, prior=prior)
        for item in proposal["evidence_bundle"]["items"]:
            eid = item["evidence_id"]
            assessments = [a for vote in valid for a in vote.get("evidence_assessments", []) if a["evidence_id"] == eid]
            irrelevant_voters = {v["voter_id"] for v in valid if any(a["evidence_id"] == eid and a["role"] == "irrelevant" for a in v.get("evidence_assessments", []))}
            if len(irrelevant_voters) >= required_votes and all(a["role"] == "irrelevant" for a in assessments) and eid not in counter_ids:
                if eid not in current_material:
                    record = {"proposal_digest": digest, "voter_ids": sorted(irrelevant_voters),
                              "rule": "independent_irrelevance_agreement", "assessments": assessments}
                    if self.state.evidence_exclusions.get(eid) != record:
                        self.state.evidence_exclusions[eid] = record
                        self.event({"issue_id": "relevance_" + eid}, "evidence_excluded", digest, evidence_id=eid, **record)

    def unresolved(self):
        return [issue for issue in self.state.issues if issue.get("status", "open") != "resolved"]
