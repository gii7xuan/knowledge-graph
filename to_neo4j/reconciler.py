"""Outbox events are wakeups, never instructions to replay stale graph mutations."""


def authoritative_item(repository, outbox):
    state = repository.current_admitted_contributions(outbox["candidate_id"])
    if not state["desired"]: return None
    c = state["desired"][0]
    return {**c["candidate"], "latest_decision": c["decision"], "evidence_items": c["evidence_bundle"]["items"]}


def dry_run(repository, *, run_id=None, scan=False):
    states = [repository.current_admitted_contributions(cid) for cid in repository.graph_pending_candidates(run_id, scan=scan)]
    return {"run_id": run_id, "candidates": len(states), "upsert": sum(bool(s["desired"]) for s in states),
            "retract": sum(not s["desired"] for s in states),
            "states": [{k: s[k] for k in ("candidate_id", "revision", "projection_version", "admission_reasons")} for s in states]}


def reconcile_candidate(repository, importer, candidate_id, *, max_passes=4):
    for _ in range(max_passes):
        state = repository.current_admitted_contributions(candidate_id)
        result = importer.reconcile(state)
        if result["status"] == "stale": continue
        repository.acknowledge_graph(candidate_id, state["projection_version"], state["state_hash"])
        current = repository.current_admitted_contributions(candidate_id)
        if current["projection_version"] == state["projection_version"] and current["state_hash"] == state["state_hash"]: return result
    raise RuntimeError("candidate changed repeatedly; reconciliation remains pending")


def apply_outbox(repository, importer, *, run_id=None, scan=False):
    result = {"completed": 0, "failed": 0, "stale": 0, "errors": {}}
    for cid in repository.graph_pending_candidates(run_id, scan=scan):
        with repository.connect() as conn:
            old_events = [r[0] for r in conn.execute("SELECT event_id FROM import_outbox WHERE candidate_id=? AND status IN ('pending','failed')", (cid,))]
        try:
            reconcile_candidate(repository, importer, cid)
            with repository.transaction(immediate=True) as conn:
                for eid in old_events: conn.execute("UPDATE import_outbox SET status='completed',attempts=attempts+1,last_error=NULL WHERE event_id=?", (eid,))
            result["completed"] += 1
        except Exception as exc:
            with repository.transaction(immediate=True) as conn:
                conn.execute("UPDATE graph_outbox SET status='failed',attempts=attempts+1,last_error=? WHERE candidate_id=? AND status IN ('pending','failed')", (str(exc), cid))
            result["failed"] += 1
            result["errors"][cid] = str(exc)
    return result
