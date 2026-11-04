"""Explicit recovery of original frozen slots; never a force re-audit."""
import copy
import json
import time
from uuid import uuid4
from .contracts import canonical_json
from .request_execution import service_key


def seed_service_pauses(repository,voters):
    """Import historic known billing failures once, until a same-service probe succeeds."""
    with repository.connect() as c:
        failures=[json.loads(r[0]) for r in c.execute("SELECT vote_json FROM validation_requests WHERE status='failed' AND json_extract(vote_json,'$.pause_job')=1")]
    for voter in voters:
        key=service_key(voter)
        with repository.connect() as c:
            known=c.execute('SELECT 1 FROM service_pauses WHERE service_key=?',(key,)).fetchone()
        failure=next((v for v in failures if v.get('model_id')==voter['model_id']),None)
        if failure and not known: repository.pause_model_service(key,{'reason_code':'HISTORICAL_INSUFFICIENT_BALANCE','model_id':voter['model_id']})


def probe_services(repository,voters):
    """Explicit operator action; no document content is sent."""
    results=[]
    for voter in voters:
        key=service_key(voter)
        response=voter['client'].complete_json({'task_type':'service_availability_probe','instruction':'Return {"ok":true}.'},
                                              system_prompt='Service availability check.',max_tokens=32)
        reported=str(response.returned_model_id or '').casefold()
        expected=str(voter['model_id']).casefold()
        if not response.response_id or reported!=expected:
            raise ValueError('service probe must report the exact configured model identity')
        result={'service_key':key,'response_status':'ok','response_id':response.response_id,'model_id':voter['model_id'],
                'input_tokens':response.input_tokens,'output_tokens':response.output_tokens}
        repository.resume_model_service(key,result);results.append(result)
    return {'items':results,'paused_jobs_require_explicit_resume':True}


def recover_frozen_audit(repository,orchestrator,candidate_id,audit_id,*,reason,extra_calls,extra_tokens,extra_seconds):
    from .case_budget import CaseBudget
    from .validators import judge_validator,model_request_count
    from .runtime import AgentRuntime
    from .agent_state import AgentState
    from .repository import ConflictError
    from .validation_recovery import recovery_plan
    audit=repository.get_audit(candidate_id,audit_id)
    saved=(audit.get('agent_state') or {})
    if not saved.get('state'):
        # A deterministic review freezes proposals/votes but has no planner
        # checkpoint. Reconstruct only from its authoritative selected proposal,
        # with all source/identity hashes checked by the normal resolver.
        if audit.get('context',{}).get('executed_strategy')!='deterministic':
            raise ValueError('original frozen checkpoint required')
        from .proposal_resolver import selected_proposal
        candidate=repository.get_revision(candidate_id,audit['revision'])
        decision=repository.latest_decision(candidate_id) or {}
        if decision.get('audit_id')!=audit_id:
            raise ConflictError('deterministic recovery requires the current selected audit')
        proposal,_=selected_proposal(repository,candidate,decision)
        from .agent_state import AgentState
        from .consensus import aggregate_votes
        restored=AgentState(candidate=candidate,context=copy.deepcopy(audit['context']),audit_id=audit_id,
            executed_strategy='deterministic')
        restored.bundle=copy.deepcopy(proposal['evidence_bundle'])
        restored.archive=list({item['evidence_id']:copy.deepcopy(item) for p in audit['rounds'] for item in p['evidence_bundle']['items']}.values())
        restored.guard=copy.deepcopy(proposal['ontology_check']);restored.ontology_checked=True
        restored.issues=copy.deepcopy(audit['context'].get('issue_history',[]))
        restored.evidence_exclusions=copy.deepcopy(audit['context'].get('evidence_exclusions',{}))
        restored.protected_ids=sorted({eid for issue in restored.issues if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])})
        restored.context['recovery_state_provenance']={'kind':'verified_selected_deterministic_proposal',
            'proposal_digest':proposal['proposal_digest'],'original_planner_checkpoint_present':False}
        restored_votes=[vote for vote in audit['votes'] if vote['round_id']==proposal['round_id']]
        restored.validations[restored.validation_key()]={'round_id':proposal['round_id'],
            'proposal_digest':proposal['proposal_digest'],'evidence_hash':proposal['evidence_bundle']['evidence_hash'],
            'votes':restored_votes,'consensus':aggregate_votes(restored_votes,orchestrator.committee,
                proposal=proposal,proposal_digest=proposal['proposal_digest'])}
        saved={'state':restored.export(),'budget':{'usage':decision.get('budget',{})}}
    state=AgentState(**saved['state'])
    current=repository.get_candidate(candidate_id)
    if current['revision']!=state.candidate['revision']: raise ConflictError('candidate revision changed; retain results only as history')
    decision=repository.latest_decision(candidate_id)
    if decision and decision.get('audit_id')!=audit_id:
        raise ConflictError('a newer audit decision already exists; do not overwrite it with historical recovery')
    frozen=state.context.get('committee_members')
    if frozen!=orchestrator.committee_members(): raise ConflictError('configured models differ from original frozen roster')
    if not state.current_validation() and audit['rounds']:
        from .consensus import aggregate_votes
        from .validators import vote_binding
        from .request_cache import validation_request_key
        proposal=audit['rounds'][-1]
        if proposal['candidate']!=state.candidate or proposal['evidence_bundle']['evidence_hash']!=state.bundle.get('evidence_hash'):
            raise ConflictError('checkpoint differs from original frozen proposal')
        restored={v['voter_id']:v for v in audit['votes'] if v['round_id']==proposal['round_id']}
        for voter in orchestrator.validators:
            if voter['id'] in restored: continue
            generation=next((g for g in proposal.get('validator_generation',[]) if g['voter_id']==voter['id']),None)
            frozen_voter={**voter,**({'max_output_tokens':generation['max_output_tokens']} if generation else {})}
            key=validation_request_key(frozen_voter,proposal,legacy_generation=not generation and proposal.get('response_contract_version')=='validator_response_v3')
            entry=repository.validation_request(key)
            restored[voter['id']]={**vote_binding(voter,proposal),'request_key':key if entry else None,
                'response_status':'api_error','outcome_unknown':not entry or entry['status'] in {'running','unknown'},
                'error_code':'HISTORICAL_SLOT_RECONCILIATION_REQUIRED','actual_calls':0}
        votes=list(restored.values())
        state.validations[state.validation_key()]={'round_id':proposal['round_id'],'proposal_digest':proposal['proposal_digest'],
            'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'votes':votes,
            'consensus':aggregate_votes(votes,orchestrator.committee,proposal=proposal,proposal_digest=proposal['proposal_digest'])}
    plan=recovery_plan(state,repository,allow_permanent_failure=True)
    if not plan['recoverable_voter_ids']:
        return {'status':'waiting_request_reconciliation' if plan['unknown_voter_ids'] else 'no_recovery_required',**plan}
    if any(repository.service_pause(service_key(v)) for v in orchestrator.validators if v['id'] in plan['recoverable_voter_ids']):
        return {'status':'service_paused','same_model_probe_required':True,**plan}
    required_calls=sum(not (repository.validation_request(v.get('request_key')) or {}).get('status')=='completed'
                       for v in state.current_validation()['votes'] if v['voter_id'] in plan['recoverable_voter_ids'])
    if extra_calls<required_calls: raise ValueError('explicit recovery budget does not cover selected failed seats')
    grant=repository.authorize_recovery_budget(audit_id,reason,extra_calls,extra_tokens,extra_seconds)
    case=repository.ensure_case(candidate_id)
    budget=CaseBudget({'max_llm_calls_per_candidate':extra_calls,'total_timeout_seconds':extra_seconds,
                      'max_total_tokens':extra_tokens},repository,[case],task_type='validation_recovery',task_id=grant)
    state.context.update(repository.get_audit(candidate_id,audit_id)['context'])
    validation=state.current_validation()
    if not validation: raise ValueError('current frozen validation is missing')
    proposal=next(p for p in audit['rounds'] if p['round_id']==validation['round_id'])
    votes={v['voter_id']:copy.deepcopy(v) for v in validation['votes']}
    def save_progress(phase):
        combined={k:saved.get('budget',{}).get('usage',{}).get(k,0)+budget.usage.get(k,0) for k in budget.usage}
        state.validations[state.validation_key()]['votes']=list(votes.values())
        state.context['agent_budget']={'usage':combined,'recovery_grant_id':grant,'previous_usage_preserved':True}
        repository.update_audit_context(audit_id,state.context)
        repository.save_agent_checkpoint(audit_id,state.export(),{**budget.snapshot(),'usage':combined},phase)
        return combined
    for voter in orchestrator.validators:
        if voter['id'] not in plan['recoverable_voter_ids']: continue
        old=votes.get(voter['id'],{})
        key=old.get('request_key')
        if not key: raise ValueError('historic request key missing; reconcile before recovery')
        entry=repository.validation_request(key)
        if entry and entry['status']=='completed':
            from .validators import vote_binding
            from .validator_contracts import validate_vote
            recovered={**copy.deepcopy(entry['vote']),**vote_binding(voter,proposal),'cached':True,'actual_calls':0,'attempts':[]}
            if validate_vote(recovered,proposal['response_contract_version']): raise ValueError('late result failed original contract')
        else:
            if not budget.reserve_calls(1): raise ValueError('recovery budget exhausted')
            owner=uuid4().hex
            claimed=repository.claim_validation_request(key,owner)
            if claimed['status']!='claimed': continue
            # Frozen v3 originally had no output cap. Keep that generation setting
            # when recovering its exact request, rather than inventing a new cache key.
            history=repository.request_history(key)
            generation=(history[-1]['metadata'].get('generation') if history else None)
            cap=generation['max_output_tokens'] if generation else next((g['max_output_tokens'] for g in proposal.get('validator_generation',[]) if g['voter_id']==voter['id']),
                None if proposal.get('response_contract_version')=='validator_response_v3' else voter.get('max_output_tokens',4096))
            legacy={**voter,'max_output_tokens':cap}
            if hasattr(voter['client'],'thinking_mode'):
                from .llm_clients import OpenAICompatibleClient
                client=voter['client']
                legacy['client']=OpenAICompatibleClient(api_key=client.api_key,base_url=client.base_url,
                    model_id=client.model_id,model_family=client.model_family,timeout_seconds=client.timeout_seconds,
                    verify_ssl=client.verify_ssl,thinking_mode=(generation or {}).get('thinking_mode'),
                    stream_responses=getattr(client,'stream_responses',False))
            feedback=None
            if old.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'} and not old.get('outcome_unknown'):
                version=proposal.get('response_contract_version')
                if version=='validator_response_v4':
                    from .validator_contracts import contract_for
                    schema=contract_for(version).RESPONSE_SCHEMA['properties']['schema_assessment']['properties']
                    constraints={'schema_assessment':'Return schema_assessment as the single schema assessment; do not output schema_status, schema_issue or checks.ontology.',
                        'schema_status_allowed':schema['status']['enum'],'schema_issue_allowed':schema['issue']['enum'],
                        'checks':'Exactly triple_support, qualifiers, relation_direction and counterevidence.',
                        'coverage':'not_covered is an issue, not a status. Missing coverage is unknown; an actual constraint violation remains fail. Do not change a substantive judgment merely to satisfy shape.'}
                else:
                    constraints={'ontology_state':'For frozen v3, checks.ontology.status must agree with schema_status; unknown schema coverage is not a failed constraint. Preserve the frozen response contract.',
                        'schema_failure':'A constraint violation is distinct from a not-covered relation.'}
                feedback={'response_status':old['response_status'],'error':old.get('error'),
                    'response_contract_version':version,
                    'validation_errors':old.get('validation_errors',[]),
                    'invalid_evidence_ids':old.get('invalid_evidence_ids',[]),
                    'raw_response_ref':old.get('attempt_id'),
                    'field_constraints':constraints,
                    'instruction':'Correct only the original JSON contract/reference failure for the same frozen candidate and evidence. Do not change identifiers or seek an approval outcome.'}
            recovered=judge_validator(legacy,proposal,repository=repository,_request_owner=owner,_request_key=key,
                deadline_epoch=budget.deadline_epoch,max_retries=0,_initial_feedback=feedback)
            repository.finish_validation_request(key,owner,recovered)
            budget.refund_calls(1-model_request_count(recovered))
            for attempt in recovered.get('attempts') or [recovered]: budget.tokens(attempt)
        repository.record_recovered_vote(audit_id,proposal,recovered)
        votes[voter['id']]=recovered
        save_progress('recovering_validation')
        if recovered.get('pause_job'):
            repository.finish_incomplete_audit(audit_id,'paused',{'reason_code':recovered.get('error_code'),'recovery_grant_id':grant})
            with repository.transaction(immediate=True) as c:
                c.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',
                          (canonical_json({'status':'paused','budget':budget.snapshot()}),grant))
            return {'status':'paused','paused':True,'recovery_grant_id':grant,'reason_code':recovered.get('error_code')}
    runtime=AgentRuntime(orchestrator);runtime.state=state;runtime.budget=budget
    from .issue_ledger import IssueLedger
    IssueLedger(state).inherit_reviewed_revision_evidence(repository)
    runtime.tools.accept_validation(proposal,list(votes.values()),state.validation_key())
    state.context.update(issue_history=copy.deepcopy(state.issues),
        unresolved_issues=copy.deepcopy(IssueLedger(state).unresolved()),
        evidence_exclusions=copy.deepcopy(state.evidence_exclusions))
    state.context['execution_row_version']=current['row_version']
    state.context['recovery_grant_id']=grant
    combined=save_progress('recovering_validation')
    current_validation=state.current_validation()
    result=orchestrator._finalize(state.candidate,state.context,audit_id,state.guard,state.bundle,
        list(votes.values()),current_validation['consensus'],runtime.policy.final_gaps(state),proposal['round_id'],combined)
    with repository.transaction(immediate=True) as c:
        c.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',(canonical_json(result),grant))
    state.workflow_state=result.get('workflow_state',state.workflow_state)
    save_progress('finished_recovery')
    return {**result,'recovery_grant_id':grant,'unknown_request_voter_ids':plan['unknown_voter_ids'],'previous_usage_preserved':True}
