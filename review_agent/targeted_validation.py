"""One bounded field patch, never a full-opinion retry after format failure."""
from copy import deepcopy
from dataclasses import replace
import json
import time
from uuid import uuid4
from .contracts import sha256_json
from . import response_corrections as repairs
from .llm_clients import response_metadata


def terminal(vote,reason,code='RESPONSE_CORRECTION_UNAVAILABLE'):
    return {**vote,'correction_terminal':True,'correction_available':False,'retryable':False,
        'correction_stop_reason':reason,'error_code':vote.get('error_code',code) if vote.get('outcome_unknown') or vote.get('pause_job') else code}


def prepare(vote,proposal,policy):
    from .validators import _parse_content
    if vote.get('response_status')=='ok':return None,None,['valid substantive votes are immutable']
    if vote.get('outcome_unknown'):return None,None,['original request outcome unknown']
    try:original=_parse_content(vote.get('raw_response'))
    except (ValueError,TypeError):return None,None,['no reliably parsed original judgment; manual review required']
    plan,errors=repairs.make_plan(original,proposal,policy)
    return original,plan,errors


def judge_repair_once(voter,proposal,feedback):
    from .validators import _parse_content,vote_binding,_normalize_review_result
    from .validator_contracts import contract_for
    from .output_structure import frozen_profile,response_format
    from .execution import api_error_fields
    base=vote_binding(voter,proposal);contract=contract_for(proposal['response_contract_version'])
    original=feedback['original_response'];plan=feedback['repair_plan'];patch_response=None
    try:
        if plan['policy']!=proposal['correction_policy'] or plan['policy'].get('prompt_hash')!=repairs.PROMPT_HASH or plan['proposal_digest']!=proposal['proposal_digest'] or plan['evidence_hash']!=proposal['evidence_bundle']['evidence_hash']:
            raise ValueError('field repair differs from frozen task or policy')
        if sha256_json({k:v for k,v in plan.items() if k!='plan_hash'})!=plan['plan_hash']:raise ValueError('field repair plan hash mismatch')
        profile=frozen_profile(voter,proposal,contract.RESPONSE_SCHEMA)
        payload=repairs.repair_payload(original,plan,proposal)
        if profile['mode']=='json_schema':payload.pop('output_template',None)
        wire=response_format(profile,repairs.repair_schema(plan))
        if wire['type']=='json_schema':wire['json_schema']['name']='triple_review_field_repair_v1'
        patch_response=voter['client'].complete_json(payload,system_prompt=repairs.PROMPT,
            compact_json=plan['policy'].get('version')=='targeted_review_repairs_v2',
            max_tokens=plan['policy']['limits']['max_output_tokens'],
            timeout_seconds=min(plan['policy']['limits']['timeout_seconds'],voter.get('http_timeout_seconds',60)),response_format=wire)
        if getattr(patch_response,'finish_reason',None) in {'length','max_tokens','incomplete'}:
            return terminal({**base,'response_status':'output_incomplete','verdict':None,
                'error':'provider reported incomplete field patch','error_category':'output_incomplete',
                'raw_response':deepcopy(feedback['original_raw_response']),
                'raw_correction_response':deepcopy(patch_response.content),**response_metadata(patch_response)},
                'bounded field correction was truncated; original opinion retained','RESPONSE_CORRECTION_FAILED')
        if isinstance(patch_response.content,str) and not patch_response.content.strip():
            return terminal({**base,'response_status':'invalid_json','verdict':None,
                'error_code':'EMPTY_MODEL_RESPONSE','error':'provider returned no visible field patch JSON',
                'raw_response':deepcopy(feedback['original_raw_response']),
                'raw_correction_response':patch_response.content,**response_metadata(patch_response)},
                'bounded field correction was empty; original opinion retained','EMPTY_MODEL_RESPONSE')
        patch=_parse_content(patch_response.content)
        merged=repairs.apply_repairs(original,plan,patch)
        result=_normalize_review_result(voter,proposal,merged,replace(patch_response,content=merged),contract)
        result.update(raw_response=deepcopy(feedback['original_raw_response']),corrected_response=deepcopy(merged),
            raw_correction_response=deepcopy(patch_response.content),response_origin='field_patch_merge',
            correction_trace={'plan':deepcopy(plan),'patch':deepcopy(patch),'original_attempt_id':feedback.get('original_attempt_id'),
                'correction_id':feedback.get('correction_id'),'protected_judgments_unchanged':True})
        if result['response_status']!='ok':result=terminal(result,'field patch did not satisfy the unchanged review contract','RESPONSE_CORRECTION_FAILED')
        return result
    except Exception as exc:
        patch_response=patch_response or getattr(exc,'llm_response',None)
        fields=api_error_fields(exc)
        result={**base,'response_status':'invalid_contract' if isinstance(exc,ValueError) else 'api_error','verdict':None,
            'error':str(exc),'raw_response':deepcopy(feedback['original_raw_response']),
            'raw_correction_response':getattr(patch_response,'content',None),
            'input_tokens':getattr(patch_response,'input_tokens',None),'output_tokens':getattr(patch_response,'output_tokens',None),
            **response_metadata(patch_response),
            **({} if isinstance(exc,ValueError) else fields)}
        return terminal(result,'bounded field correction failed; original opinion retained','RESPONSE_CORRECTION_FAILED')


def judge_targeted(voter,proposal,*,max_retries,timeout,repository,deadline,owner,key):
    from .validators import vote_binding,_bounded_attempt,model_request_count
    from .request_execution import durable_attempt
    from .request_identity import semantic_request_key
    from .validator_contracts import contract_for
    from .output_structure import frozen_profile,response_format
    from .correction_repository import reserve,allowance
    policy=proposal['correction_policy'];limits=policy['limits'];history=repository.request_history(key) if repository else []
    attempts=[];initial_count=sum((r['metadata'].get('feedback') or {}).get('kind') not in {'field_patch','contradiction_clarification'} for r in history)
    prior=deepcopy(history[-1]['result']) if history else None
    max_calls=1+min(max(0,int(max_retries)),1)
    final=prior or {**vote_binding(voter,proposal),'response_status':'api_error','verdict':None,'error_code':'DEADLINE_EXHAUSTED','actual_calls':0}
    def dispatch(feedback=None):
        cap=min(float(voter.get('request_timeout_seconds',timeout)),deadline-time.time() if deadline else float('inf'))
        if feedback:cap=min(cap,float(limits['timeout_seconds']))
        if cap<=0:return {**vote_binding(voter,proposal),'response_status':'api_error','not_sent':True,'actual_calls':0,'retryable':False,'error_code':'REQUEST_DEADLINE_EXHAUSTED'}
        current={**voter,'http_timeout_seconds':min(float(voter.get('http_timeout_seconds',cap)),cap)}
        if repository:
            return durable_attempt(repository,key,owner,vote_binding(current,proposal),current,proposal,feedback,cap,
                lambda:__import__('review_agent.validators',fromlist=['_judge_validator_once'])._judge_validator_once(current,proposal,feedback))
        return _bounded_attempt(current,proposal,feedback,cap)
    if not prior or prior.get('response_status') in {'api_error','timeout'} and not prior.get('outcome_unknown') and not prior.get('raw_response'):
        while len(attempts)<max_calls and initial_count<limits['max_initial_attempts_per_seat']:
            final=dispatch();attempts.append(final);initial_count+=1
            if final.get('response_status') not in {'api_error','timeout'} or final.get('retryable') is False:break
        if initial_count>=limits['max_initial_attempts_per_seat'] and final.get('response_status') in {'api_error','timeout'} and not final.get('outcome_unknown'):
            final=terminal(final,'initial transport attempt limit reached','INITIAL_REQUEST_LIMIT')
    if final.get('response_status')=='ok' or final.get('outcome_unknown'):pass
    elif final.get('correction_terminal'):pass
    elif final.get('response_status') in {'invalid_json','invalid_contract','invalid_reference'}:
        original,plan,blocked=prepare(final,proposal,policy)
        conflicts=repairs._conflicts(original) if isinstance(original,dict) else []
        if blocked and conflicts and policy.get('semantic_clarification'):
            from .semantic_clarification import make_plan
            plan=make_plan(original,proposal,policy,conflicts)
            blocked=[]
        if blocked:final=terminal(final,'; '.join(blocked))
        elif not limits['max_corrections_per_seat']:final=terminal(final,'field corrections disabled','RESPONSE_CORRECTION_EXHAUSTED')
        elif len(attempts)<max_calls and (history or max_retries>0):
            why=allowance(repository,key,proposal['candidate'],policy) if repository else None
            if why:final=terminal(final,why,'RESPONSE_CORRECTION_EXHAUSTED')
            else:
                contract=contract_for(proposal['response_contract_version']);profile=frozen_profile(voter,proposal,contract.RESPONSE_SCHEMA)
                clarify=plan.get('kind')=='contradiction_clarification'
                if clarify:
                    from . import semantic_clarification as clarification
                    payload=clarification.payload(original,plan,proposal)
                    prompt=clarification.PROMPT
                    wire={'type':'json_object'}
                else:
                    payload=repairs.repair_payload(original,plan,proposal)
                    prompt=repairs.PROMPT
                    wire=response_format(profile,repairs.repair_schema(plan))
                if profile['mode']=='json_schema':payload.pop('output_template',None)
                compact=plan['policy'].get('version')=='targeted_review_repairs_v2'
                input_bytes=len(json.dumps({'system':prompt,'payload':payload,'response_format':wire},ensure_ascii=False,
                    **({'separators':(',',':')} if compact else {})).encode('utf-8'))
                reserve_tokens=input_bytes+1024+limits['max_output_tokens']
                if input_bytes>limits['max_input_bytes'] or reserve_tokens>limits['max_reserved_tokens_per_candidate_version'] or reserve_tokens>voter.get('correction_token_budget',float('inf')):
                    final=terminal(final,'field correction payload exceeds frozen input/token budget','RESPONSE_CORRECTION_BUDGET')
                else:
                    ident,why=(reserve(repository,semantic_request_key('triple_review',voter,proposal),key,proposal,plan,reserve_tokens)
                        if repository else ('memory_'+uuid4().hex,None))
                    if why:final=terminal(final,why,'RESPONSE_CORRECTION_BUDGET')
                    else:
                        feedback={'kind':'contradiction_clarification' if clarify else 'field_patch','repair_plan':plan,'original_response':original,
                            'original_raw_response':deepcopy(final['raw_response']),'original_attempt_id':final.get('attempt_id'),
                            'correction_id':ident,'reserved_token_upper_bound':reserve_tokens}
                        if clarify:feedback['original_vote']=deepcopy(final)
                        final=dispatch(feedback)
                        if clarify:
                            final['raw_response']=deepcopy(feedback['original_raw_response'])
                            final.setdefault('semantic_clarification',{'version':clarification.VERSION,
                                'status':'outcome_unknown' if final.get('outcome_unknown') else 'failed',
                                'original_response_hash':plan['original_response_hash'],
                                'original_judgments_immutable':True,'counts_as_vote':False})
                            final=terminal(final,'内部矛盾已请求澄清；保留原判断，等待人工复核','SEMANTIC_CLARIFICATION_REQUIRED')
                        attempts.append(final)
        else:final={**final,'correction_available':True,'retryable':False}
    if not attempts:
        final={**final,'cached':True,'input_tokens':0,'output_tokens':0}
    return {**final,'attempts':attempts,'attempt_no':len(attempts),'actual_calls':sum(model_request_count(a) for a in attempts)}
