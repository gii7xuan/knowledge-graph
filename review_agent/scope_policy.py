"""Knowledge selection never rewrites source truth; no sentence-keyword filters."""
from .contracts import sha256_json
from copy import deepcopy
import json
from pathlib import Path

DEFAULT_POLICY = {'version':'knowledge_scope_v1','excluded_scopes':['local_definition','derivation','example']}
SCOPES = {'general','conditional','local_definition','derivation','example','unresolved'}


def task_policy():
    return json.loads((Path(__file__).resolve().parents[1]/'specs/knowledge_scope_v2.json').read_text(encoding='utf-8'))


def validate_policy(policy):
    if not isinstance(policy,dict) or policy.get('version') not in {'knowledge_scope_v1','knowledge_scope_v2'}:
        raise ValueError('unsupported knowledge scope policy')
    excluded=policy.get('excluded_scopes')
    if not isinstance(excluded,list) or any(not isinstance(s,str) for s in excluded) or len(excluded)!=len(set(excluded)) or set(excluded)- (SCOPES-{'unresolved'}):
        raise ValueError('invalid excluded knowledge scopes')
    if policy['version']=='knowledge_scope_v2':
        if set(policy)-{'version','task_goal','excluded_scopes','categories','unresolved_action','decision_authority'}:
            raise ValueError('scope policy cannot contain candidate-specific overrides')
        categories=policy.get('categories',{})
        if not isinstance(categories,dict) or set(categories)!=SCOPES or any(not isinstance(v,dict) or set(v)!={'label','boundary'} or
            not all(isinstance(t,str) and t.strip() for t in v.values()) for v in categories.values()):
            raise ValueError('every knowledge category requires a label and boundary')
        if (not isinstance(policy.get('task_goal'),str) or not policy['task_goal'].strip() or
                policy.get('unresolved_action')!='scope_review' or policy.get('decision_authority')!='program'):
            raise ValueError('task goal, program authority and unresolved scope handoff required')
    return deepcopy(policy)


def classification_decision(knowledge_scope, confirmed, policy=None):
    policy=validate_policy(policy if policy is not None else DEFAULT_POLICY)
    status=('exclude' if knowledge_scope in policy['excluded_scopes'] else 'include') if confirmed and knowledge_scope in SCOPES-{'unresolved'} else 'unclear'
    category=policy.get('categories',{}).get(knowledge_scope,{})
    return {'status':status,'knowledge_scope':knowledge_scope,'classification_confirmed':bool(confirmed),
        'policy_version':policy['version'],'policy_hash':sha256_json(policy),'authority':'program',
        'category_label':category.get('label'),'boundary':category.get('boundary'),
        'reason':('类别已确认，按任务配置排除' if status=='exclude' else '类别已确认，属于任务收录范围' if status=='include' else '类别未确认或审核席位不完整，等待范围复核')}


def scope_route(source_support, expression_status, mapping_status, knowledge_scope, *, policy=None,
                scope_confirmed=False, real_gap_confirmed=False):
    policy = validate_policy(policy if policy is not None else DEFAULT_POLICY)
    classification=classification_decision(knowledge_scope,scope_confirmed,policy)
    binding = {'scope_policy_version':policy['version'],'scope_policy_hash':sha256_json(policy),
               'source_support':source_support,'knowledge_scope':knowledge_scope,'classification':classification}
    if source_support=='contradicted': state='source_error'
    elif source_support!='supported': state='evidence_review'
    elif expression_status!='faithful': state='revision_required'
    elif classification['status']=='exclude': state='excluded_by_scope'
    elif mapping_status in {'ambiguous','unassigned','constraint_conflict'}: state='mapping_review'
    elif mapping_status=='not_covered': state='ontology_extension' if real_gap_confirmed else 'mapping_review'
    elif mapping_status=='matched' and scope_confirmed: state='eligible_for_existing_admission'
    else: state='scope_review'
    return {**binding,'workflow_state':state,'preserve_source_record':True,
            'excluded_from_main_graph':state=='excluded_by_scope'}
