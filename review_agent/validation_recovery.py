"""Recover technical failures selected by the server, preserving every valid vote."""
import copy
from .validators import judge_independently, model_request_count


def recovery_plan(state, repository, *, allow_permanent_failure=False):
    validation=state.current_validation()
    if not validation: return {'recoverable_voter_ids':[], 'unknown_voter_ids':[]}
    votes={v['voter_id']:v for v in validation['votes']}
    recoverable,unknown=[],[]
    blocked=[]
    targeted=state.context.get('response_contract_version')in {'validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}
    policy=state.context.get('correction_policy') or {}
    for member in state.context.get('committee_members',[]):
        vote=votes.get(member['id'])
        if vote and vote.get('response_status')=='ok': continue
        entry=repository.validation_request(vote['request_key']) if vote and vote.get('request_key') else None
        if (vote and vote.get('error_code')=='PERMANENT_API_ERROR' and not allow_permanent_failure
                and not (entry and entry['status']=='completed')):
            continue
        if entry and entry['status'] in {'running','unknown'}:
            unknown.append(member['id'])
        elif vote and vote.get('outcome_unknown') and not (entry and entry['status']=='completed'):
            unknown.append(member['id'])
        else:
            if targeted and not (entry and entry['status']=='completed'):
                reason=vote.get('correction_stop_reason') if vote and vote.get('correction_terminal') else None
                if not reason and vote and vote.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'}:
                    from .correction_repository import allowance
                    reason=allowance(repository,vote.get('request_key'),state.candidate,policy) if policy else 'frozen correction policy unavailable'
                if reason:
                    blocked.append({'voter_id':member['id'],'reason':reason});continue
            recoverable.append(member['id'])
    return {'recoverable_voter_ids':recoverable,'unknown_voter_ids':unknown,
            'valid_votes_immutable':True,'selection_by':'server_frozen_roster',
            **({'blocked_voters':blocked} if targeted else {})}


def recover_validation(runtime):
    state,budget=runtime.state,runtime.budget
    plan=recovery_plan(state,runtime.repository)
    if not plan['recoverable_voter_ids']:
        return {'status':'waiting_request_reconciliation','recovery':plan,'model_calls':0}
    validation=state.current_validation()
    proposal=next(p for p in runtime.repository.get_audit(state.candidate['candidate_id'],state.audit_id)['rounds']
                  if p['round_id']==validation['round_id'])
    voters=[v for v in runtime.orchestrator.validators if v['id'] in plan['recoverable_voter_ids']]
    if not budget.reserve_calls(len(voters)):
        return {'status':'budget_exhausted','error':'no remaining call budget for failed seats'}
    runtime.checkpoint('recovering_validation')
    votes=judge_independently(voters,proposal,max_retries=0,
        per_call_timeout_seconds=float(budget.config.get('per_call_timeout_seconds',45)),
        repository=runtime.repository,deadline_epoch=budget.deadline_epoch)
    budget.refund_calls(len(voters)-sum(model_request_count(v) for v in votes))
    merged={v['voter_id']:copy.deepcopy(v) for v in validation['votes']}
    for vote in votes:
        for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
        runtime.repository.record_recovered_vote(state.audit_id,proposal,vote)
        merged[vote['voter_id']]=vote
    return runtime.tools.accept_validation(proposal,list(merged.values()),state.validation_key())
