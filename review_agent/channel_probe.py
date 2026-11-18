"""Durable synthetic transport diagnostics; never a semantic review vote."""
from .contracts import sha256_json

VERSION='synthetic_channel_probe_v1'


def binding(voter,proposal):
    return {'task_type':VERSION,'task_id':proposal['task_id'],'object_revision':1,
        'proposal_digest':proposal['proposal_digest'],'voter_id':voter['id'],
        'model_id':voter['model_id'],'model_family':voter['model_family'],
        'model_identity':str(voter.get('model_identity') or voter['model_id']).casefold()}


def judge_once(voter,proposal,feedback=None):
    from .validators import _parse_content
    from .llm_clients import response_metadata
    from .execution import api_error_fields
    from .transport_diagnostics import safe_error_text
    from .schema_validation import schema_errors
    result=binding(voter,proposal)
    if sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal['proposal_digest']:
        return {**result,'response_status':'api_error','not_sent':True,'actual_calls':0,'retryable':False,'error':'probe hash mismatch'}
    response=None
    try:
        response=voter['client'].complete_json(proposal['wire_payload'],system_prompt=proposal['system_prompt'],
            response_format=proposal['response_format'],max_tokens=proposal['max_output_tokens'],timeout_seconds=180,
            user_suffix=proposal.get('user_suffix',''))
        result.update(response_metadata(response),raw_response=response.content)
        if response.finish_reason in {'length','max_tokens','incomplete'}:
            return {**result,'response_status':'output_incomplete','validation_errors':['incomplete output'],
                'counts_as_review_vote':False}
        parsed=_parse_content(response.content)
        errors=schema_errors(parsed,proposal['schema'])
        if proposal['kind']=='nonce' and parsed!={'probe_token':proposal['expected_token']}:
            errors.append('synthetic token mismatch')
        if proposal['kind']=='v8' and not errors:
            from .response_contract_v8 import validate_response
            from .response_consistency import issues
            errors+=validate_response(parsed)
            result['consistency_issues']=issues(parsed)
            if result['consistency_issues']:errors.append('synthetic review response consistency failed')
        if response.finish_reason in {'length','max_tokens','incomplete'}:errors.append('incomplete output')
        result.update(response_status='invalid_contract' if errors else 'ok',validation_errors=errors,
            counts_as_review_vote=False)
    except Exception as exc:
        result.update(response_metadata(response),response_status='invalid_contract' if response else 'api_error',
            error=safe_error_text(exc,voter['client']),**({} if response else api_error_fields(exc)),counts_as_review_vote=False)
    return result
