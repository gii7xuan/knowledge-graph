"""Bounded explanations of contradictory output; never replacement review votes."""
from copy import deepcopy
import hashlib
from .contracts import sha256_json
from .schema_validation import schema_errors, fixed_template

VERSION='review_contradiction_clarification_v2'
PROMPT='你只澄清同一份审核输出内部的矛盾，不重新投票。说明原判断、证据角色和结论为何不一致，区分找到正面支持与未发现反证。引用冻结材料中的实际证据ID；材料不足时明确无法消除矛盾。不得返回通过票、修改原判断或为了格式合规而改变事实结论。只返回规定JSON。'
SCHEMA={'type':'object','additionalProperties':False,'required':['explanation','positive_support','counterevidence','unresolved_questions'],
    'properties':{'explanation':{'type':'string','minLength':1},
        'positive_support':{'type':'object','additionalProperties':False,'required':['status','evidence_ids'],
            'properties':{'status':{'enum':['found','not_found','unresolved']},'evidence_ids':{'type':'array','items':{'type':'string'}}}},
        'counterevidence':{'type':'object','additionalProperties':False,'required':['status','evidence_ids'],
            'properties':{'status':{'enum':['found','not_found','unresolved']},'evidence_ids':{'type':'array','items':{'type':'string'}}}},
        'unresolved_questions':{'type':'array','items':{'type':'string','minLength':1}}}}


def policy():
    return {'version':VERSION,'prompt_hash':hashlib.sha256(PROMPT.encode()).hexdigest(),
        'schema_hash':sha256_json(SCHEMA),'mode':'explanation_only','admission_authorized':False}


def make_plan(original,proposal,correction_policy,conflicts):
    plan={'version':VERSION,'kind':'contradiction_clarification','original_response_hash':sha256_json(original),
        'proposal_digest':proposal['proposal_digest'],'evidence_hash':proposal['evidence_bundle']['evidence_hash'],
        'policy':deepcopy(correction_policy),'conflicts':conflicts}
    plan['plan_hash']=sha256_json(plan)
    return plan


def payload(original,plan,proposal):
    return {'task_type':VERSION,'original_response':deepcopy(original),'conflicts':plan['conflicts'],
        'candidate':proposal['candidate'],'evidence':proposal['evidence_bundle']['items'],
        'output_schema':SCHEMA,'output_template':fixed_template(SCHEMA),
        'instruction':'只解释矛盾；原始票仍为无效并等待人工复核，澄清不能获得准入。'}


def pending(vote):
    """Read-only compatibility projection, including old unclarified outputs."""
    from .validators import _parse_content
    from .response_corrections import _conflicts
    try:original=_parse_content(vote.get('raw_response'))
    except (ValueError,TypeError):return None
    if not isinstance(original,dict):return None
    conflicts=_conflicts(original)
    if not conflicts:return None
    return {'voter_id':vote.get('voter_id'),'status':'human_clarification_required','conflicts':conflicts,
        'original_response_hash':sha256_json(original),'original_judgments_immutable':True,
        'clarification':deepcopy(vote.get('semantic_clarification')),'counts_as_vote':False}


def judge_once(voter,proposal,feedback):
    from .validators import _parse_content
    from .targeted_validation import terminal
    from .execution import api_error_fields
    original=feedback['original_response'];plan=feedback['repair_plan'];response=None
    result=deepcopy(feedback['original_vote'])
    result.pop('attempts',None)
    clarification={'version':VERSION,'status':'failed','counts_as_vote':False,
        'original_response_hash':sha256_json(original),'original_judgments_immutable':True}
    try:
        if (plan['policy']!=proposal['correction_policy'] or plan['policy'].get('semantic_clarification')!=policy() or
            sha256_json({k:v for k,v in plan.items() if k!='plan_hash'})!=plan['plan_hash'] or
            plan['proposal_digest']!=proposal['proposal_digest'] or plan['evidence_hash']!=proposal['evidence_bundle']['evidence_hash'] or
            plan['original_response_hash']!=sha256_json(original)):
            raise ValueError('clarification frozen binding mismatch')
        response=voter['client'].complete_json(payload(original,plan,proposal),system_prompt=PROMPT,
            max_tokens=plan['policy']['limits']['max_output_tokens'],
            timeout_seconds=min(plan['policy']['limits']['timeout_seconds'],voter.get('http_timeout_seconds',60)),
            response_format={'type':'json_object'})
        answer=_parse_content(response.content)
        errors=schema_errors(answer,SCHEMA)
        if errors:raise ValueError('; '.join(errors))
        allowed={e['evidence_id'] for e in proposal['evidence_bundle']['items']}
        for name in ('positive_support','counterevidence'):
            item=answer[name]
            if set(item['evidence_ids'])-allowed or (item['status']=='found')!=bool(item['evidence_ids']):
                raise ValueError('clarification observation requires exact source references')
        if set(answer['positive_support']['evidence_ids']) & set(answer['counterevidence']['evidence_ids']):
            raise ValueError('clarification cannot assign conflicting roles to the same source')
        clarification.update(status='explanation_recorded',answer=answer)
    except Exception as exc:
        clarification['error']=str(exc)
        if not isinstance(exc,ValueError):result.update(api_error_fields(exc))
    result.update(semantic_clarification=clarification,
        raw_clarification_response=deepcopy(getattr(response,'content',None)),
        input_tokens=getattr(response,'input_tokens',None),output_tokens=getattr(response,'output_tokens',None))
    # A convincing explanation still cannot replace an internally invalid ballot.
    return terminal(result,'内部矛盾已请求澄清；保留原判断，等待人工复核','SEMANTIC_CLARIFICATION_REQUIRED')
