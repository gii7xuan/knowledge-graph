"""Requests own their lifecycle independently of a candidate's waiting deadline."""
import os
import subprocess
import sys
import threading
import time
import json
from .contracts import sha256_json
import copy
from uuid import uuid4


def service_key(voter):
    client=voter['client']
    return sha256_json([str(getattr(client,'base_url','')).rstrip('/'),
                        voter.get('model_id') or getattr(client,'model_id','')])


def judge_typed(voter, proposal, repository, *, task_type, binding, invoke, validate_cached,
                prompt_hash, retries=0, timeout=45, deadline_epoch=None):
    """Shared single-flight execution; task contracts and frozen keys remain separate."""
    client=voter['client']
    semantic={k:v for k,v in proposal.items() if k not in {'audit_id','round_id','proposal_digest'}}
    if task_type=='ontology_review':
        # Review-context reminders cannot reroll an unchanged schema/source request.
        # Cached approvals are still checked against every current protected ID.
        semantic={k:v for k,v in semantic.items() if k not in {'prior_counter_evidence_ids','human_review_context'}}
    key=sha256_json(['typed_validation_v1',task_type,semantic,prompt_hash,binding.get('model_identity'),
                    binding.get('model_family'),str(getattr(client,'base_url','')).rstrip('/'),
                    sha256_json(getattr(client,'api_key','')),int(voter.get('max_output_tokens',4096)),
                    type(client).__module__+'.'+type(client).__qualname__,
                    *([{'thinking_mode':client.thinking_mode}] if getattr(client,'thinking_mode',None) else [])])
    owner=uuid4().hex
    from .request_identity import semantic_request_key
    entry=repository.claim_validation_request(key,owner,
        semantic_key=semantic_request_key(task_type,voter,proposal,prompt_hash))
    if entry.get('blocking_request_key'):
        return {**binding,'response_status':'api_error','error_code':'RELATED_REQUEST_OUTCOME_UNKNOWN',
            'outcome_unknown':True,'retryable':False,'actual_calls':0,'attempts':[],'request_key':key,
            'blocking_request_key':entry['blocking_request_key'],'error':'equivalent request is still unresolved'}
    if entry['status']!='claimed':
        vote=copy.deepcopy(entry.get('vote') or {})
        if entry['status']=='completed':
            vote.update(binding)
            errors=validate_cached(vote)
            if not errors:
                return {**vote,'cached':True,'actual_calls':0,'attempts':[], 'request_key':key,
                        'input_tokens':0,'output_tokens':0}
        return {**binding,'response_status':'api_error','error_code':'DUPLICATE_REQUEST_SUPPRESSED',
                'error':'frozen request running or outcome unknown','outcome_unknown':True,
                'retryable':False,'cached':True,'actual_calls':0,'attempts':[],'request_key':key}
    attempts=[]
    feedback=None
    history=repository.request_history(key)
    prior=history[-1] if history else None
    failed=(prior or {}).get('result') or {}
    if (prior and prior.get('state')=='failed' and not failed.get('outcome_unknown')
            and failed.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'}):
        feedback={'error':failed.get('error'),'response_status':failed['response_status'],
            'raw_response_ref':prior['attempt_id'],'validation_errors':failed.get('validation_errors',[]),
            'instruction':'Correct the original contract/reference error for this exact frozen task. Preserve IDs, evidence, constraints and substantive uncertainty; do not seek an approval outcome.'}
        if task_type=='ontology_review':
            feedback['requested_context_contract']={'allowed':None,'if_needed_exact_keys':['query','scope','limit'],
                'scope_values':['current_chunk','same_section','parent_section','other_section'],'limit_range':[1,6]}
            feedback['requested_context_instruction']='Use null when no context request is needed. Otherwise use only query (nonempty string), scope and limit. Preserve revise/reject/unknown and the original substantive concerns; changing JSON shape is not evidence for approval.'
            feedback['approval_necessity_rules']={'confirmed_gap':'a necessary missing term, definition or constraint, including a missing annotation on an existing formal target',
                'reuse_existing':'the existing schema already suffices without the proposed delta','not_needed':'no ontology change is needed'}
            feedback['necessity_consistency_instruction']='The frozen contract requires confirmed_gap for approve. Reusing an existing target while filling a justified missing definition is different from reuse_existing without any delta. If the change is unnecessary or its necessity is uncertain, preserve that substantive judgment and use revise/reject/uncertain instead of making an unsupported gap claim. Do not change the assessment merely to get approval.'
        if task_type=='ontology_competency_review':
            feedback['question_ids']=[q['id'] for q in proposal.get('questions',[])]
            feedback['quotation_rule']='Quote 8..1200 characters exactly from one frozen source document, with exactly one occurrence. Do not normalize spaces, punctuation or LaTeX. Choose a longer exact span when a quotation occurs more than once. For fail/unknown, quotes may be empty when no exact quotation is available. Preserve the actual pass/fail/unknown assessment and coverage; quotation repair is not evidence for changing the outcome.'
            previous=failed.get('raw_response') or {}
            if isinstance(previous,str):
                try:previous=json.loads(previous)
                except ValueError:previous={}
            documents={d['document_id']:d['text'] for d in proposal.get('source_documents',[])}
            feedback['invalid_quotation_references']=[]
            for assessment in previous.get('assessments',[]) if isinstance(previous,dict) else []:
                if not isinstance(assessment,dict):continue
                quotes=assessment.get('quotes',[])
                if not isinstance(quotes,list):continue
                for quote in quotes:
                    if not isinstance(quote,dict):continue
                    document=documents.get(quote.get('document_id'),'');literal=quote.get('quote')
                    count=document.count(literal) if isinstance(literal,str) and literal else 0
                    if count!=1:
                        feedback['invalid_quotation_references'].append({'question_id':assessment.get('question_id'),
                            'document_id':quote.get('document_id'),'previous_quote':literal,'exact_occurrences':count})
        if task_type=='candidate_revision_review':
            feedback['required_changed_fields']=proposal.get('changed_fields',[])
            feedback['required_reviewed_evidence_ids']=proposal.get('protected_evidence_ids',[])
            feedback['protected_material_rule']='Independently assess every protected_evidence_id, including supporting/context material. counterevidence_reviewed_ids records which protected items were assessed, not which items actually contradict the revision. Approval still requires all protected items assessed; do not add a contradictory item to support merely to pass the contract.'
            try:
                previous=json.loads(failed.get('raw_response') or '{}')
                reviewed=previous.get('counterevidence_reviewed_ids',[]) if isinstance(previous,dict) else []
                reviewed={value for value in reviewed if isinstance(value,str)} if isinstance(reviewed,list) else set()
            except (TypeError,ValueError):reviewed=set()
            feedback['missing_reviewed_evidence_ids']=sorted(set(proposal.get('protected_evidence_ids',[]))-reviewed)
            feedback['missing_review_rule']='These IDs were absent from your previous counterevidence_reviewed_ids. Supporting and new source items may also be protected. If you actually assess an item, list its ID in counterevidence_reviewed_ids as well as any appropriate supporting list. This does not make it counterevidence: counter_evidence_ids must contain only actual contradictions. Do not claim an assessment you did not perform.'
            after=proposal.get('after',{}).get('triple',{})
            feedback['field_status_rules_for_approval']={field:('not_stated_in_scope' if
                field.split('.')[0] in {'qualifiers','qualifier_status'} and
                after.get('qualifier_status',{}).get(field.split('.')[1])=='not_stated' else 'supported')
                for field in proposal.get('changed_fields',[])}
            feedback['field_status_instruction']='These are the original contract conditions for approval, not a requested outcome. An evidence-grounded removal of an unsupported type mapping is assessed as an operation; not_stated_in_scope is reserved for qualifier absence. Return uncertain/reject when the operation is not grounded. Every approved field review must cite actual allowed source evidence.'
    result={**binding,'response_status':'api_error','error_code':'DEADLINE_EXHAUSTED','actual_calls':0}
    for number in range(retries+1):
        wait=min(float(voter.get('request_timeout_seconds',timeout)),
                 max(0.,deadline_epoch-time.time()) if deadline_epoch else timeout)
        if wait<=0: break
        if deadline_epoch:
            voter={**voter,'http_timeout_seconds':min(float(voter.get('http_timeout_seconds',getattr(voter['client'],'timeout_seconds',wait))),
                                                     max(.001,deadline_epoch-time.time()))}
        result=durable_attempt(repository,key,owner,binding,voter,proposal,feedback,wait,
                              lambda:invoke(feedback),task_type=task_type)
        attempts.append({**result,'attempt_no':number+1})
        if result['response_status']=='ok' or result.get('retryable') is False or result.get('pause_job'): break
        feedback={'error':result.get('error'),'raw_response_ref':result.get('attempt_id'),
                  'instruction':'Repair contract/reference failures for exactly the same frozen task. Do not change IDs or facts.'}
    result={**result,'attempts':attempts,'request_key':key,
            'actual_calls':sum(0 if a.get('actual_calls')==0 else 1 for a in attempts)}
    repository.finish_validation_request(key,owner,result)
    return result


def durable_attempt(repository, key, owner, binding, voter, proposal, feedback, timeout, invoke, *, task_type='triple_review'):
    from .llm_clients import OpenAICompatibleClient
    started=time.monotonic()
    skey=service_key(voter)
    paused=repository.service_pause(skey)
    if paused:
        return {**binding,'response_status':'api_error','verdict':None,'pause_job':True,
                'error_code':'SERVICE_PAUSED','retryable':False,'actual_calls':0,'error':'model service is paused'}
    metadata={'binding':binding,'task_type':task_type,'proposal':proposal,'feedback':feedback,'service_key':skey,
              'generation':{'max_output_tokens':voter.get('max_output_tokens',4096),
                  **({'ontology_delivery_version':voter['ontology_delivery_version']} if voter.get('ontology_delivery_version') else {}),
                  **({'stream_responses':True} if getattr(voter['client'],'stream_responses',False) else {}),
                  **({'thinking_mode':voter['client'].thinking_mode} if getattr(voter['client'],'thinking_mode',None) else {})}}
    if task_type=='triple_review' and proposal.get('response_contract_version') in {'validator_response_v6_1','validator_response_v6_2','validator_response_v6_3'}:
        generation=next((g for g in proposal.get('validator_generation',[]) if g.get('voter_id')==voter['id']),{})
        metadata['generation']['output_transport']=copy.deepcopy(generation.get('output_transport'))
    if proposal.get('response_contract_version')in {'validator_response_v6_2','validator_response_v6_3'} and (feedback or {}).get('kind')=='field_patch':
        metadata['generation']['max_output_tokens']=feedback['repair_plan']['policy']['limits']['max_output_tokens']
        metadata['generation']['request_phase']='field_patch'
        metadata['generation']['reserved_token_upper_bound']=feedback['reserved_token_upper_bound']
    attempt_id=repository.begin_request_attempt(key,owner,max(.001,timeout-(time.monotonic()-started)),metadata)
    if time.monotonic()-started>=timeout:
        result={**binding,'response_status':'api_error','error_code':'REQUEST_DEADLINE_EXHAUSTED',
                'not_sent':True,'actual_calls':0,'retryable':False,'error':'deadline reached before request dispatch'}
        repository.finish_request_attempt(attempt_id,owner,result)
        return result
    client=voter['client']
    if isinstance(client,OpenAICompatibleClient):
        # Independent OS process survives termination of the candidate process.
        # Credentials travel only through an anonymous pipe, not argv or the DB.
        descriptor={'database':str(repository.database_path.resolve()),'request_key':key,'owner':owner,
            'attempt_id':attempt_id,'task_type':task_type,
            'voter':{k:voter[k] for k in ('id','model_id','model_family','model_identity','request_timeout_seconds','http_timeout_seconds','ontology_delivery_version') if k in voter},
            'client':{**{k:getattr(client,k) for k in ('api_key','base_url','model_id','model_family','timeout_seconds','verify_ssl')},
                      'timeout_seconds':voter.get('http_timeout_seconds',client.timeout_seconds)},
            'max_output_tokens':metadata['generation']['max_output_tokens']}
        descriptor['client']['thinking_mode']=getattr(client,'thinking_mode',None)
        descriptor['client']['stream_responses']=getattr(client,'stream_responses',False)
        try:
            process=subprocess.Popen([sys.executable,'-m','review_agent.request_worker'],stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=str(__import__('pathlib').Path(__file__).resolve().parents[1]),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=os.environ.copy())
        except OSError as exc:
            result={**binding,'response_status':'api_error','error_code':'REQUEST_NOT_SENT','not_sent':True,
                    'actual_calls':0,'retryable':True,'error':str(exc)}
            repository.finish_request_attempt(attempt_id,owner,result)
            return result
        try:
            process.stdin.write(json.dumps(descriptor,ensure_ascii=False).encode('utf-8'))
            process.stdin.close()
        except (OSError,BrokenPipeError) as exc:
            process.terminate()
            result={**binding,'response_status':'api_error','error_code':'DISPATCH_OUTCOME_UNKNOWN',
                'outcome_unknown':True,'retryable':False,'error':str(exc)}
            repository.finish_request_attempt(attempt_id,owner,result)
            return result
    else:
        def run():
            try:
                result=invoke()
                if result.get('pause_job'): repository.pause_model_service(skey,result)
                repository.finish_request_attempt(attempt_id,owner,result,late=time.time()>deadline)
            except Exception:
                # A failed persistence path stays fenced; it must never trigger a second request.
                pass
        deadline=time.time()+max(0.,timeout-(time.monotonic()-started))
        threading.Thread(target=run,daemon=True,name='durable-mock-request').start()
    while time.monotonic()-started < timeout:
        row=repository.request_attempt(attempt_id)
        if row['state'] in {'completed','failed','not_sent'} or row['state']=='unknown' and row.get('result'):
            return row['result']
        time.sleep(min(.025,max(.001,timeout-(time.monotonic()-started))))
    result={**binding,'response_status':'timeout','verdict':None,'retryable':False,'outcome_unknown':True,
        'error_code':'HARD_REQUEST_TIMEOUT','error':'caller wait deadline reached; original request may complete later',
        'attempt_id':attempt_id,'request_key':key,'latency_ms':int((time.monotonic()-started)*1000)}
    repository.finish_request_attempt(attempt_id,owner,result)
    latest=repository.request_attempt(attempt_id)
    return latest['result'] if latest['state'] in {'completed','failed'} else result
