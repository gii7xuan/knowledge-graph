"""Re-evaluate server obligations using existing votes; prepare gated extension tasks."""
from copy import deepcopy
from pathlib import Path
import json
from review_agent.agent_state import AgentState
from review_agent.cli import _configured_validators
from review_agent.consensus import aggregate_votes
from review_agent.issue_ledger import IssueLedger
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository
from review_agent.runtime import AgentRuntime
from review_agent.service import load_config
from review_agent.extension_eligibility import extension_eligibility

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030/obligation_reconciliation'
OUT.mkdir(exist_ok=True)


if __name__=='__main__':
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    source=json.loads((ROOT/'runs/review_followup_20261030/required_reaudit/status.json').read_text(encoding='utf-8'))
    receipts=[]
    for row in source['results']:
        if not row.get('independent_review_completed'): continue
        candidate=repo.get_revision(row['candidate_id']);current=repo.get_candidate(row['candidate_id'])
        audit=repo.get_audit(row['candidate_id'],row['audit_id'])
        if current['revision']!=row['revision'] or current['human_status']!='pending': continue
        if repo.latest_decision(row['candidate_id'])['audit_id']!=row['audit_id']: continue
        proposal=audit['rounds'][-1]
        votes=[v for v in audit['votes'] if v['round_id']==proposal['round_id']]
        guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or repo.get_run(candidate['run_id'])['ontology_path']))
        orchestrator=ReviewOrchestrator(repo,guard,_configured_validators(config),config=config)
        consensus=aggregate_votes(votes,orchestrator.committee,proposal=proposal,proposal_digest=proposal['proposal_digest'])
        if set(consensus['eligible_voter_ids'])!=set(orchestrator.committee.voter_ids): continue
        state=AgentState(candidate,deepcopy(audit['context']),audit['audit_id'])
        state.issues=deepcopy(audit['context'].get('issue_history',[]))
        state.evidence_exclusions=deepcopy(audit['context'].get('evidence_exclusions',{}))
        ledger=IssueLedger(state)
        for issue in state.issues:
            old_origin=issue.get('opened_revision',issue.get('inherited_from_revision'))
            restored=ledger.origin_revision(issue,repo,audit['revision'])
            if old_origin!=restored: ledger.event(issue,'origin_revision_restored',previous_origin=old_origin,origin_revision=restored)
        ledger.observe(proposal,votes,consensus,orchestrator.committee.required_votes)
        grant=repo.authorize_recovery_budget(audit['audit_id'],'服务器依据原有完整三席票重新核验问题起始版本与解决条件，不重新调用模型',0,0,60)
        context=repo.get_audit(candidate['candidate_id'],audit['audit_id'])['context']
        context.update(issue_history=state.issues,unresolved_issues=ledger.unresolved(),
            issue_resolution_policy_version='immutable_issue_origin_v2',server_reconciliation_grant=grant)
        repo.record_issue_events(audit['audit_id'],state.issue_events)
        repo.update_audit_context(audit['audit_id'],context)
        gaps=deepcopy(proposal.get('gaps',{}));gaps['critical_issues_unresolved']=any(i.get('critical') for i in ledger.unresolved())
        result=orchestrator._finalize(candidate,context,audit['audit_id'],proposal['ontology_check'],proposal['evidence_bundle'],votes,
            consensus,gaps,proposal['round_id'],repo.latest_decision(candidate['candidate_id']).get('budget',{}))
        with repo.transaction(immediate=True) as c:
            c.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',(json.dumps({'model_calls':0,'result':result},ensure_ascii=False),grant))
        receipt={'candidate_id':candidate['candidate_id'],'revision':candidate['revision'],'model_calls':0,
            'workflow_state':result['workflow_state'],'resolved_issues':[i['code'] for i in state.issues if i.get('status')=='resolved'],
            'remaining_issues':[i['code'] for i in ledger.unresolved()]}
        runtime=AgentRuntime(orchestrator);runtime.state=state
        state.bundle=proposal['evidence_bundle'];state.archive=deepcopy(proposal['evidence_bundle']['items'])
        runtime.tools.inspect_ontology(None,'新版本完整复核后核对本体复用及缺口',surface_relation=candidate['triple'].get('surface_relation'))
        if state.guard!=proposal['ontology_check']: raise RuntimeError('ontology diagnostics changed; do not rebind original votes')
        state.validations[state.validation_key()]={'round_id':proposal['round_id'],'proposal_digest':proposal['proposal_digest'],
            'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'votes':votes,'consensus':consensus}
        state.context=context
        state.context['execution_row_version']=repo.get_candidate(candidate['candidate_id'])['row_version']
        kind='add_object_property' if candidate['triple']['tail_kind']=='entity' else 'add_data_property'
        eligibility=extension_eligibility(state)
        if kind in eligibility['allowed_change_kinds']:
            account=repo.case_status(repo.ensure_case(candidate['candidate_id']))
            if account['usage']['extension_requests']>=account['limits']['extension_requests']:
                repo.authorize_case_budget(candidate['candidate_id'],{'extension_requests':1},reason='当前修订经原三席来源复核，继续本体建模调查并保留旧订阅历史',
                    actor=config.get('local_operator','local_operator'),authorization_key='post-revision-ontology:'+candidate['candidate_id']+':'+str(candidate['revision']))
            supporting=sorted({eid for v in votes for eid in v.get('supporting_evidence_ids',[])})
            pending=runtime.tools.request_ontology_extension(kind,
                '修订后的当前三席来源共识支持该表述，但冻结本体未覆盖；先调查复用、别名及最小差异，不直接认可新增约束',
                {'label':candidate['triple']['surface_relation']},supporting)
            receipt['ontology_request']=pending
            with repo.transaction(immediate=True) as c:
                jobs=c.execute("SELECT job_id FROM jobs WHERE job_type='ontology_extension' AND status='queued' AND json_extract(payload_json,'$.extension_id')=?",(pending['extension_id'],)).fetchall()
                for job in jobs:
                    c.execute("UPDATE jobs SET status='paused',progress_json=? WHERE job_id=?",(json.dumps({'reason_code':'LONG_CONTEXT_GATEWAY_504',
                        'source_revision':candidate['revision'],'source_audit_id':audit['audit_id'],'review_not_dispatched':True}),job[0]))
        else: receipt['ontology_request_eligibility']=eligibility
        receipts.append(receipt)
    (OUT/'receipt.json').write_text(json.dumps({'model_calls':0,'items':receipts},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'model_calls':0,'items':receipts},ensure_ascii=False,indent=2))
