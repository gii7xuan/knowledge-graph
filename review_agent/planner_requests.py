"""A planner action is a durable, state-bound task, never a committee vote."""
from copy import deepcopy
import json
from .contracts import sha256_json
from .planner_contracts import SYSTEM_PROMPT, validate_action

VERSION = 'planner_action_v1'


def decision_material(value):
    """Exclude execution bookkeeping from the unknown-request equivalence fence."""
    transient = {'audit_id', 'state_version', 'step_no', 'issue_id', 'event_id', 'budget',
                 'input_size', 'request_key', 'attempt_id', 'latency_ms', 'workflow_state'}
    if isinstance(value, dict):
        return {k: decision_material(v) for k, v in value.items() if k not in transient}
    if isinstance(value, list):
        return [decision_material(v) for v in value]
    return value


def proposal_for(state, payload, tools, *, system_prompt=SYSTEM_PROMPT):
    proposal = {'version': VERSION, 'audit_id': state.audit_id,
        'candidate_id': state.candidate['candidate_id'], 'candidate_revision': state.candidate['revision'],
        'candidate_hash': sha256_json(state.candidate), 'state_version': state.state_version,
        'state_hash': state.digest(), 'tool_contract_hash': sha256_json(tools),
        'input_hash': sha256_json(payload), 'decision_hash': sha256_json(decision_material(payload)),
        'payload': deepcopy(payload), 'system_prompt': system_prompt,
        'prompt_hash': sha256_json(system_prompt)}
    proposal['proposal_digest'] = sha256_json(proposal)
    return proposal


def binding(voter, proposal):
    return {'task_type': 'planner_action', 'task_id': proposal['candidate_id'],
        'object_revision': proposal['candidate_revision'], 'proposal_digest': proposal['proposal_digest'],
        **{k: proposal[k] for k in ('state_version', 'state_hash', 'tool_contract_hash', 'input_hash')},
        'response_contract_version': VERSION, 'voter_id': voter['id'],
        'model_id': voter['model_id'], 'model_family': voter['model_family'],
        'model_identity': str(voter.get('model_identity') or voter['model_id']).casefold()}


def action_errors(result, proposal):
    errors = []
    if result.get('input_hash') != proposal['input_hash']:
        errors.append('planner input differs from the frozen request')
    try:
        validate_action(result.get('action'))
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return errors


def judge_once(voter, proposal, feedback=None):
    from .validators import _parse_content
    from .execution import api_error_fields
    result = binding(voter, proposal)
    if (sha256_json({k: v for k, v in proposal.items() if k != 'proposal_digest'}) != proposal['proposal_digest']
            or sha256_json(proposal['payload']) != proposal['input_hash']
            or sha256_json(proposal['system_prompt']) != proposal['prompt_hash']):
        return {**result, 'response_status': 'api_error', 'error_code': 'PLANNER_BINDING_MISMATCH',
                'not_sent': True, 'actual_calls': 0, 'retryable': False}
    payload = deepcopy(proposal['payload'])
    if feedback:
        payload['correction_feedback'] = feedback
    try:
        response = voter['client'].complete_json(payload, system_prompt=proposal['system_prompt'],
                                                max_tokens=int(voter.get('max_output_tokens', 2048)))
        result.update(input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            raw_response=response.content, returned_model_id=getattr(response, 'returned_model_id', None),
            response_id=getattr(response, 'response_id', None), finish_reason=getattr(response, 'finish_reason', None),
            reasoning_tokens=getattr(response, 'reasoning_tokens', None), latency_ms=getattr(response, 'latency_ms', None))
        result.update(gateway_request_id=getattr(response,'gateway_request_id',None),
                      transport_metrics=deepcopy(getattr(response,'transport_metrics',None)))
        try:
            if result['finish_reason'] in {'length', 'max_tokens'}:
                raise ValueError('planner output truncated at token limit')
            result['action'] = validate_action(_parse_content(response.content))
            result['response_status'] = 'ok'
        except (ValueError, TypeError, KeyError) as exc:
            result.update(response_status='invalid_action', error=str(exc))
    except Exception as exc:
        result.update(response_status='api_error', error=str(exc), **api_error_fields(exc))
    return result


def request(repository, voter, proposal, timeout):
    from .request_execution import judge_typed
    return judge_typed(voter, proposal, repository, task_type='planner_action',
        binding=binding(voter, proposal), invoke=lambda feedback: judge_once(voter, proposal, feedback),
        validate_cached=lambda r: action_errors(r, proposal), prompt_hash=proposal['prompt_hash'],
        retries=0, timeout=timeout)


def current_action(result, proposal, state, validator=validate_action):
    if (state.state_version != proposal['state_version'] or state.digest() != proposal['state_hash']
            or sha256_json(state.candidate) != proposal['candidate_hash']):
        return None, 'stale_planner_state'
    if result.get('response_status') != 'ok' or action_errors(result, proposal):
        return None, result.get('error') or 'planner result unavailable'
    try:
        return validator(deepcopy(result['action'])), None
    except (ValueError, TypeError, KeyError) as exc:
        return None, str(exc)
