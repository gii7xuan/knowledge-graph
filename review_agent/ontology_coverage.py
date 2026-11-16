"""Explicit independent coverage assessment; low lexical similarity is never gap proof."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from .contracts import sha256_json,canonical_json

VERSION='ontology_coverage_assessment_v1'
PROMPT='Independently assess whether the exact source-supported predicate has an equivalent property in the COMPLETE frozen catalogue. Planner exclusions are hypotheses. Check every property, direction and meaning. Do not infer absence from similarity or a recalled subset. Return uncertain unless the entire comparison is justified. Definitions or entity evidence that are missing remain missing.'
SCHEMA={'type':'object','additionalProperties':False,'required':['assessment','reviewed_relation_ids','source_evidence_ids','note'],
    'properties':{'assessment':{'enum':['gap_confirmed','reuse_existing','uncertain']},
        'reviewed_relation_ids':{'type':'array','items':{'type':'string'}},
        'source_evidence_ids':{'type':'array','minItems':1,'items':{'type':'string'}},'note':{'type':'string','minLength':1}}}


def current_assessment(state):
    result=state.context.get('coverage_assessment') or {};proposal=result.get('proposal') or {}
    if (not result.get('global_gap_confirmable') or proposal.get('candidate')!=state.candidate
            or result.get('evidence_hash')!=state.bundle.get('evidence_hash')
            or proposal.get('catalog_hash')!=sha256_json(proposal.get('properties'))
            or sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal.get('proposal_digest')):return False
    members=state.context.get('committee_members',[]);votes=result.get('votes',[]);identities=set();families=set()
    for member in members:
        vote=next((v for v in votes if v.get('voter_id')==member['id']),{})
        if (vote.get('response_status')!='ok' or vote.get('assessment')!='gap_confirmed'
                or any(vote.get(k)!=v for k,v in binding(member,proposal).items())
                or errors({k:vote.get(k) for k in SCHEMA['required']},proposal)):return False
        ids={str(vote.get(k) or '').casefold() for k in ('model_identity','returned_model_id')}-{''}
        if not ids or identities&ids:return False
        identities|=ids;families.add(vote['model_family'])
    return len(members)>0 and len(families)>=len(members)


def binding(voter,proposal):
    return {'task_type':VERSION,'task_id':proposal['task_id'],'object_revision':proposal['candidate']['revision'],
        'proposal_digest':proposal['proposal_digest'],'catalog_hash':proposal['catalog_hash'],
        'voter_id':voter['id'],'model_id':voter['model_id'],'model_family':voter['model_family'],
        'model_identity':str(voter.get('model_identity') or voter['model_id']).casefold()}


def errors(raw,proposal):
    from .schema_validation import schema_errors
    problems=schema_errors(raw,SCHEMA)
    if problems:return problems
    if set(raw['reviewed_relation_ids'])!={p['id'] for p in proposal['properties']}:
        problems.append('the complete frozen property catalogue must be assessed')
    available={e['evidence_id'] for e in proposal['evidence_bundle']['items'] if e.get('anchor_valid')}
    if not set(raw['source_evidence_ids'])<=available:problems.append('source citation outside bound evidence')
    return problems


def judge_once(voter,proposal,feedback=None):
    from .validators import _parse_content
    from .execution import api_error_fields
    result=binding(voter,proposal)
    try:
        if sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal['proposal_digest']:
            return {**result,'response_status':'api_error','not_sent':True,'actual_calls':0,'retryable':False}
        response=voter['client'].complete_json({**proposal,'output_contract':SCHEMA},system_prompt=PROMPT,max_tokens=2048)
        raw=_parse_content(response.content);invalid=errors(raw,proposal)
        result.update(raw if not invalid else {'error':'; '.join(invalid)},
            response_status='invalid_contract' if invalid else 'ok',input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,raw_response=response.content,
            returned_model_id=getattr(response,'returned_model_id',None),response_id=getattr(response,'response_id',None))
    except Exception as exc:result.update(response_status='api_error',error=str(exc),**api_error_fields(exc))
    return result


def review(runtime,exclusions,search_queries):
    from schema.relation_registry import RelationRegistry
    from .planner_contracts import ActionError
    from .request_execution import judge_typed
    state=runtime.state;validation=state.current_validation() or {}
    if validation.get('consensus',{}).get('source_support_consensus',{}).get('result')!='supported':
        raise ActionError('coverage assessment requires current independent source support')
    properties=list(RelationRegistry(runtime.orchestrator.ontology_guard.ontology).properties.values())
    if state.ontology_lookup.get('scope')!='full_frozen_ontology':raise ActionError('full release search required')
    ids={p['id'] for p in properties}
    if set(e['relation_id'] for e in exclusions)!=ids:raise ActionError('every property in the full search scope needs an explicit exclusion hypothesis')
    available={e['evidence_id'] for e in state.bundle.get('items',[]) if e.get('anchor_valid')}
    if any(not e['note'] or not e['evidence_ids'] or not set(e['evidence_ids'])<=available for e in exclusions):
        raise ActionError('coverage exclusions require actual source evidence, not similarity scores')
    if state.context.get('coverage_assessment'):return state.context['coverage_assessment']
    catalog_hash=sha256_json(properties)
    task='coverage_'+sha256_json([state.candidate,catalog_hash,state.bundle.get('evidence_hash')])[:32]
    proposal={'version':VERSION,'task_id':task,'audit_id':state.audit_id,'candidate':deepcopy(state.candidate),
        'properties':properties,'catalog_hash':catalog_hash,'evidence_bundle':deepcopy(state.bundle),
        'search_queries':search_queries,'exclusion_hypotheses':exclusions,'global_scope':'all_properties_in_bound_release'}
    proposal['proposal_digest']=sha256_json(proposal)
    if len(json.dumps(proposal,ensure_ascii=False))>int(runtime.budget.config.get('max_coverage_chars',60000)):
        raise ActionError('complete catalogue exceeds coverage budget; do not replace it with subset gap proof')
    repository=runtime.repository
    with repository.transaction(immediate=True) as c:
        c.execute('CREATE TABLE IF NOT EXISTS ontology_coverage_tasks(task_id TEXT PRIMARY KEY,proposal_json TEXT NOT NULL,result_json TEXT NOT NULL)')
        previous=c.execute('SELECT result_json FROM ontology_coverage_tasks WHERE task_id=?',(task,)).fetchone()
    if previous:return json.loads(previous[0]) or {'status':'review_incomplete','automatic_resend':False}
    voters=runtime.orchestrator.validators
    if not runtime.budget.reserve_calls(len(voters)):return {'status':'budget_exhausted','model_calls':0}
    with repository.transaction(immediate=True) as c:
        inserted=c.execute('INSERT OR IGNORE INTO ontology_coverage_tasks VALUES(?,?,?)',(task,canonical_json(proposal),'{}')).rowcount
    if not inserted:
        runtime.budget.refund_calls(len(voters));return {'status':'review_incomplete','automatic_resend':False}
    def run(voter):
        voter={**voter,'case_ids':list(getattr(runtime.budget,'case_ids',[]))}
        return judge_typed(voter,proposal,repository,task_type=VERSION,binding=binding(voter,proposal),
            invoke=lambda feedback:judge_once(voter,proposal),validate_cached=lambda r:errors({k:r.get(k) for k in SCHEMA['required']},proposal),
            prompt_hash=sha256_json([PROMPT,SCHEMA]),retries=0,timeout=min(120,runtime.budget.remaining_seconds()),deadline_epoch=runtime.budget.deadline_epoch)
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:votes=list(pool.map(run,voters))
    from .validators import model_request_count
    calls=sum(model_request_count(v) for v in votes);runtime.budget.refund_calls(len(voters)-calls)
    for vote in votes:
        for attempt in vote.get('attempts') or [vote]:runtime.budget.tokens(attempt)
    valid=[v for v in votes if v.get('response_status')=='ok' and not errors({k:v.get(k) for k in SCHEMA['required']},proposal)]
    diverse=len({v['model_identity'] for v in valid})==len(voters) and len({v['model_family'] for v in valid})>=runtime.orchestrator.committee.minimum_distinct_families
    ready=diverse and len(valid)==len(voters) and {v['assessment'] for v in valid}=={'gap_confirmed'}
    result={'version':VERSION,'status':'confirmed_gap' if ready else 'unresolved','global_gap_confirmable':ready,
        'candidate_hash':sha256_json(state.candidate),'catalog_hash':catalog_hash,'evidence_hash':state.bundle.get('evidence_hash'),
        'ontology_release_id':state.candidate.get('ontology_release_id'),'proposal':proposal,'votes':votes,'model_calls':calls}
    with repository.transaction(immediate=True) as c:c.execute('UPDATE ontology_coverage_tasks SET result_json=? WHERE task_id=?',(canonical_json(result),task))
    state.context['coverage_assessment']=result
    return result
