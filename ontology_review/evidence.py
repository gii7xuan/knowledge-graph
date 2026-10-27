"""Reopen exact source proposals and verify immutable document coordinates."""
from document.hierarchy_index import load_document_index
from evidence.verification import verify_evidence_bundle, make_evidence_bundle
from review_agent.proposal_resolver import selected_proposal
from review_agent.agent_state import AgentStopped


def load_sources(repository, task):
    evidence, documents, candidates, case_ids = {}, {}, {}, []
    active = {(s["candidate_id"], s["revision"]): s for s in task["subscriptions"] if s["status"] == "active"}
    for trigger in task["trigger_candidates"]:
        key = (trigger["candidate_id"], trigger["revision"])
        subscription = active.get(key)
        if not subscription: continue
        current = repository.get_candidate(key[0])
        if current["revision"] != key[1] or current["row_version"] != subscription["expected_row_version"] or current["human_status"] != "pending": continue
        candidate = repository.get_revision(*key)
        allowed_bases = {task["base_release_id"], *[b["base_release_id"] for b in task.get("base_history", [])]}
        if candidate.get("ontology_release_id") not in allowed_bases:
            raise ValueError("extension source binding mismatch")
        audit = repository.get_audit(key[0], trigger["audit_id"])
        proposal = next((p for p in audit["rounds"] if p["round_id"] == trigger["round_id"]), None)
        if not proposal: raise ValueError("extension source proposal not found")
        proposal, _ = selected_proposal(repository, candidate, {"audit_id": trigger["audit_id"],
            "selected_round_id": trigger["round_id"], "selected_proposal_digest": trigger["source_proposal_digest"],
            "selected_evidence_hash": trigger["evidence_hash"], "committee_version": proposal["committee_version"]})
        manifest = repository.get_run(candidate["run_id"])
        document = load_document_index(manifest["document_path"], candidate.get("source_index_hash") or manifest.get("source_index_hash"),
                                       require_verified=candidate.get("contract_version") == "extraction_candidate_v4")
        verified = verify_evidence_bundle(make_evidence_bundle(proposal["evidence_bundle"]["items"], []), document, candidate)
        if verified.get("rejected_items") or not verified["items"]: raise ValueError("extension source anchor verification failed")
        for item in verified["items"]: evidence[item["evidence_id"]] = item
        documents[key[0]], candidates[key[0]] = document, candidate
        case_ids.append(repository.ensure_case(key[0]))
    if not candidates: raise AgentStopped("extension_has_no_current_subscribers")
    for item in task.get("review_evidence", []):
        if item["evidence_id"] in evidence: continue
        owner = next((identity for identity, candidate in candidates.items() if candidate["document_id"] == item.get("document_id") and candidate["document_version"] == item.get("document_version")), None)
        if owner is None: raise ValueError("previous modeling evidence has no current verified source owner")
        verified = verify_evidence_bundle(make_evidence_bundle([item], []), documents[owner], candidates[owner])
        if verified.get("rejected_items") or not verified["items"]: raise ValueError("previous modeling evidence is no longer verifiable")
        evidence[item["evidence_id"]] = verified["items"][0]
    return list(evidence.values()), documents, candidates, sorted(set(case_ids))
