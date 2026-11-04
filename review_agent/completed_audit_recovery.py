"""Commit a newer complete frozen review after a local failure, without model calls."""
from copy import deepcopy
import json
from .agent_state import AgentState
from .contracts import sha256_json
from .issue_ledger import IssueLedger
from .proposal_resolver import selected_proposal
from .repository import ConflictError
from .runtime import AgentRuntime


def restore_completed_audit(repository,orchestrator,candidate_id,audit_id,*,reason,extra_seconds=60):
    if not str(reason).strip():raise ValueError('explicit recovery reason required')
    audit=repository.get_audit(candidate_id,audit_id);current=repository.get_candidate(candidate_id)
    candidate=repository.get_revision(candidate_id)
    if audit['status'] not in {'failed','timeout','interrupted'}:raise ConflictError('only locally failed unfinished decisions may be restored')
    if repository.get_audit(candidate_id)['audit_id']!=audit_id:raise ConflictError('a newer audit already exists')
    if current['human_status']!='pending' or current['revision']!=audit['revision']:raise ConflictError('candidate was changed or adjudicated')
    saved=audit.get('agent_state') or {}
    if not saved.get('state'):raise ValueError('original server checkpoint required')
    state=AgentState(**deepcopy(saved['state']));validation=state.current_validation()
    if state.candidate!=candidate or state.context.get('execution_row_version')!=current['row_version']:
        raise ConflictError('checkpoint does not match the current candidate and row version')
    if not validation or not state.ontology_checked:raise ValueError('original selected validation and ontology check required')
    if state.pending_extension_id:raise ConflictError('pending extension requires its own lifecycle recovery')
    if state.context.get('mode')!='shadow' or orchestrator.config.get('routing',{}).get('auto_accept_enabled'):
        raise ValueError('zero-call restoration is limited to shadow, without admission')
    decision=repository.latest_decision(candidate_id) or {}
    if decision.get('audit_id')!=audit_id and decision.get('created_at','')>audit.get('created_at',''):
        raise ConflictError('a newer decision cannot be overwritten')
    frozen=next((p for p in audit['rounds'] if p['round_id']==validation['round_id']),None)
    if not frozen or frozen['proposal_digest']!=validation['proposal_digest']:raise ValueError('checkpoint selected proposal is missing')
    binding={'audit_id':audit_id,'selected_round_id':frozen['round_id'],'selected_proposal_digest':frozen['proposal_digest'],
        'selected_evidence_hash':state.bundle.get('evidence_hash'),'committee_version':frozen['committee_version']}
    proposal,votes=selected_proposal(repository,candidate,binding)
    if (state.guard!=proposal['ontology_check'] or state.bundle!=proposal['evidence_bundle']
            or proposal['committee_version']!=orchestrator.committee.version
            or state.context.get('committee_members')!=orchestrator.committee_members()
            or {v['voter_id'] for v in votes}!=set(orchestrator.committee.voter_ids)
            or any(v.get('outcome_unknown') for v in votes)):
        raise ValueError('complete original frozen committee and exact checkpoint binding required')
    from .admission import load_admission_artifacts
    from evidence.verification import verify_evidence_bundle
    release,document=load_admission_artifacts(repository,candidate)
    if any(candidate.get(k)!=release.manifest.get(r) for k,r in
           [('ontology_release_id','release_id'),('ontology_version','ontology_version'),('profile_hash','profile_hash')]):
        raise ValueError('current source/ontology artifacts do not match original review')
    verified=verify_evidence_bundle(proposal['evidence_bundle'],document,candidate)
    if verified.get('rejected_items') or len(verified['items'])!=len(proposal['evidence_bundle']['items']):
        raise ValueError('original evidence anchors are no longer verifiable')
    before={v['voter_id']:sha256_json(v) for v in audit['votes'] if v['round_id']==proposal['round_id']}
    grant=repository.authorize_recovery_budget(audit_id,reason,0,0,extra_seconds)
    committed=False
    try:
        operational=repository.get_audit(candidate_id,audit_id)['context']
        for name in ('job_id','recovery_grant_id','recovery_deadline_history','execution_deadline_epoch','recovery_of_job_id'):
            if name in operational:state.context[name]=operational[name]
        state.context.update(execution_row_version=current['row_version'],
            completed_validation_recovery={'grant_id':grant,'method':'verified_current_checkpoint_zero_call_commit',
                'proposal_digest':proposal['proposal_digest'],'original_failure':audit['context'].get('execution_failure'),
                'model_calls':0,'original_votes_unchanged':True})
        ledger=IssueLedger(state);ledger.inherit_reviewed_revision_evidence(repository)
        runtime=AgentRuntime(orchestrator);runtime.state=state
        runtime.tools.accept_validation(proposal,votes,state.validation_key())
        state.context.update(issue_history=deepcopy(state.issues),unresolved_issues=deepcopy(ledger.unresolved()),
            evidence_exclusions=deepcopy(state.evidence_exclusions))
        gaps=runtime.policy.final_gaps(state);gaps['stop_reason']='recovered_completed_validation_after_local_failure'
        repository.update_audit_context(audit_id,state.context)
        usage=deepcopy(saved.get('budget',{}).get('usage',{}))
        result=orchestrator._finalize(candidate,state.context,audit_id,state.guard,state.bundle,votes,
            state.current_validation()['consensus'],gaps,proposal['round_id'],usage)
        if not result['committed']:raise ConflictError('candidate changed during zero-call restoration')
        committed=True
        state.workflow_state=result['workflow_state'];state.context['execution_row_version']=repository.get_candidate(candidate_id)['row_version']
        repository.save_agent_checkpoint(audit_id,state.export(),saved.get('budget',{}),'finished_zero_call_recovery')
        after=repository.get_audit(candidate_id,audit_id)
        if before!={v['voter_id']:sha256_json(v) for v in after['votes'] if v['round_id']==proposal['round_id']}:
            raise RuntimeError('original model votes changed during restoration')
        with repository.transaction(immediate=True) as connection:
            connection.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',
                (json.dumps({'model_calls':0,'result':result},ensure_ascii=False),grant))
        return {**result,'model_calls':0,'recovery_grant_id':grant,'original_votes_unchanged':True}
    except Exception as exc:
        if not committed:
            repository.finish_incomplete_audit(audit_id,'failed',{'reason_code':'ZERO_CALL_RESTORATION_FAILED','error':str(exc),'recovery_grant_id':grant})
        raise
