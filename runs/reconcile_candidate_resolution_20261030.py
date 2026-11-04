"""Recompute issue projections from unchanged current votes; no model calls."""
from pathlib import Path
from copy import deepcopy
import json
from review_agent.agent_state import AgentState
from review_agent.cli import _configured_validators
from review_agent.consensus import aggregate_votes
from review_agent.issue_ledger import IssueLedger
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.proposal_resolver import selected_proposal
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.progress import write_status_snapshot

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'


def main():
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config();voters=_configured_validators(config)
    plans=[]
    for name in ('revision_plan.json','followup_revision_plan.json','supplement_revision_plan.json',
                 'continuation_revision_plan.json','source_mapping_revision_plan.json','final_followup_revision_plan.json'):
        if (OUT/name).exists():plans+=json.loads((OUT/name).read_text(encoding='utf-8'))['items']
    receipts=[]
    for identity in dict.fromkeys(p['candidate_id'] for p in plans):
        current=repo.get_candidate(identity);candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
        if not decision or current['human_status']!='pending':continue
        audit=repo.get_audit(identity,decision['audit_id'])
        if not audit['context'].get('independent_review_completed'):continue
        proposal,votes=selected_proposal(repo,candidate,decision)
        guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or repo.get_run(candidate['run_id'])['ontology_path']))
        orchestrator=ReviewOrchestrator(repo,guard,voters,config=config)
        consensus=aggregate_votes(votes,orchestrator.committee,proposal=proposal,proposal_digest=proposal['proposal_digest'])
        if set(consensus['eligible_voter_ids'])!=set(orchestrator.committee.voter_ids):continue
        state=AgentState(candidate,deepcopy(audit['context']),audit['audit_id'])
        state.issues=deepcopy(audit['context'].get('issue_history',[]))
        state.evidence_exclusions=deepcopy(audit['context'].get('evidence_exclusions',{}))
        ledger=IssueLedger(state)
        ledger.inherit_reviewed_revision_evidence(repo)
        for issue in state.issues:ledger.origin_revision(issue,repo,audit['revision'])
        ledger.observe(proposal,votes,consensus,orchestrator.committee.required_votes)
        if state.issues==audit['context'].get('issue_history',[]) and state.evidence_exclusions==audit['context'].get('evidence_exclusions',{}):continue
        prior_status={issue['issue_id']:issue.get('status','open') for issue in audit['context'].get('issue_history',[])}
        semantic_change=(state.evidence_exclusions!=audit['context'].get('evidence_exclusions',{}) or
            any(prior_status.get(issue['issue_id'])!=issue.get('status','open') for issue in state.issues))
        if not semantic_change:
            repo.record_issue_events(audit['audit_id'],state.issue_events)
            metadata={'issue_history':state.issues,'unresolved_issues':ledger.unresolved()}
            if state.context.get('revision_evidence_provenance'):metadata['revision_evidence_provenance']=state.context['revision_evidence_provenance']
            repo.update_audit_context(audit['audit_id'],metadata)
            receipts.append({'candidate_id':identity,'audit_id':audit['audit_id'],'model_calls':0,'metadata_only':True,
                'candidate_row_version_unchanged':True})
            continue
        grant=repo.authorize_recovery_budget(audit['audit_id'],'依原有完整三席票同步问题账本，不重新调用模型、不改变原票或提案',0,0,60)
        context=repo.get_audit(identity,audit['audit_id'])['context']
        context.update(issue_history=state.issues,unresolved_issues=ledger.unresolved(),evidence_exclusions=state.evidence_exclusions,
            execution_row_version=current['row_version'],server_issue_reconciliation_grant=grant)
        if state.context.get('revision_evidence_provenance'):context['revision_evidence_provenance']=state.context['revision_evidence_provenance']
        repo.record_issue_events(audit['audit_id'],state.issue_events);repo.update_audit_context(audit['audit_id'],context)
        gaps=deepcopy(proposal.get('gaps',{}));unresolved=ledger.unresolved()
        conflict=any(i['code'] in {'source_conflict','counterevidence'} for i in unresolved)
        gaps.update(critical_issues_unresolved=any(i.get('critical') for i in unresolved),
            unresolved_counterevidence=conflict,unresolved_support_conflict=conflict,
            need_more_evidence=any(i['code'] in {'EVIDENCE_INSUFFICIENT','source_conflict','counterevidence'} for i in unresolved))
        result=orchestrator._finalize(candidate,context,audit['audit_id'],proposal['ontology_check'],proposal['evidence_bundle'],
            votes,consensus,gaps,proposal['round_id'],decision.get('budget',{}))
        if not result['committed']:raise RuntimeError('source audit changed during reconciliation')
        with repo.transaction(immediate=True) as connection:
            connection.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',(json.dumps({'model_calls':0,'committed':True}),grant))
        receipts.append({'candidate_id':identity,'audit_id':audit['audit_id'],'model_calls':0,'workflow_state':result['workflow_state'],
            'resolved_issues':[i['code'] for i in state.issues if i.get('status')=='resolved'],
            'open_issues':[i['code'] for i in unresolved]})
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    write_status_snapshot(OUT/('issue_reconciliation_'+stamp+'.json'),{'model_calls':0,'items':receipts})
    print(json.dumps({'model_calls':0,'updated':len(receipts),'items':receipts},ensure_ascii=False))


if __name__=='__main__':main()
