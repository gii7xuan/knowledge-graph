"""Explicit streaming extraction contract; original v1 replies retain their meaning."""
from .candidate_contract import RESPONSE_SCHEMA
from review_agent.contracts import sha256_json

VERSION='source_candidate_extraction_v2'
POLICY='small_source_streaming_v2'


def binding(voter,proposal):
    return {'task_type':VERSION,'task_id':proposal['task_id'],'object_revision':1,
        'proposal_digest':proposal['proposal_digest'],'voter_id':voter['id'],'model_id':voter['model_id'],
        'model_family':voter['model_family'],
        'model_identity':str(voter.get('model_identity') or voter['model_id']).casefold()}


def judge_once(voter,proposal,feedback=None):
    from review_agent.llm_clients import response_metadata
    from review_agent.validators import _parse_content
    from review_agent.execution import api_error_fields
    from review_agent.schema_validation import schema_errors
    from review_agent.transport_diagnostics import safe_error_text
    result={**binding(voter,proposal),'counts_as_review_vote':False,'counts_as_human_label':False}
    if (proposal.get('delivery_policy')!=POLICY or
            sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal['proposal_digest'] or
            proposal.get('schema_hash')!=sha256_json(RESPONSE_SCHEMA)):
        return {**result,'response_status':'api_error','not_sent':True,'actual_calls':0,
                'retryable':False,'error':'frozen streaming extraction input mismatch'}
    if not getattr(voter['client'],'stream_responses',False):
        return {**result,'response_status':'api_error','not_sent':True,'actual_calls':0,
                'retryable':False,'error':'extraction v2 requires a streaming client'}
    response=None
    try:
        response=voter['client'].complete_json(proposal['payload'],system_prompt=proposal['system_prompt'],
            response_format={'type':'json_object'},max_tokens=proposal['max_output_tokens'],
            timeout_seconds=voter.get('http_timeout_seconds',180),compact_json=True,
            on_progress=voter.get('on_stream_progress'))
        result.update(response_metadata(response),raw_response=response.content)
        if response.finish_reason in {'length','max_tokens','incomplete'}:
            return {**result,'response_status':'output_incomplete','retryable':False,
                    'validation_errors':['known terminal output truncation; complete prefix items may be recovered separately']}
        parsed=_parse_content(response.content);errors=schema_errors(parsed,RESPONSE_SCHEMA)
        return {**result,'response_status':'invalid_contract' if errors else 'ok','validation_errors':errors}
    except Exception as exc:
        result.update(response_metadata(response),response_status='invalid_contract' if response else 'api_error',
            raw_response=getattr(response,'content',None),error=safe_error_text(exc,voter['client']),
            **({} if response else api_error_fields(exc)))
        if response is None and getattr(exc,'llm_response',None) is not None:
            result.update(outcome_unknown=True,retryable=False,error_code='EXTRACTION_STREAM_OUTCOME_UNKNOWN')
        return result
