"""Knowledge selection never rewrites source truth; no sentence-keyword filters."""
from .contracts import sha256_json

DEFAULT_POLICY = {'version':'knowledge_scope_v1','excluded_scopes':['local_definition','derivation','example']}


def scope_route(source_support, expression_status, mapping_status, knowledge_scope, *, policy=None,
                scope_confirmed=False, real_gap_confirmed=False):
    policy = policy or DEFAULT_POLICY
    binding = {'scope_policy_version':policy['version'],'scope_policy_hash':sha256_json(policy),
               'source_support':source_support,'knowledge_scope':knowledge_scope}
    if source_support=='contradicted': state='source_error'
    elif source_support!='supported': state='evidence_review'
    elif expression_status!='faithful': state='revision_required'
    elif scope_confirmed and knowledge_scope in policy.get('excluded_scopes',[]): state='excluded_by_scope'
    elif mapping_status in {'ambiguous','unassigned','constraint_conflict'}: state='mapping_review'
    elif mapping_status=='not_covered': state='ontology_extension' if real_gap_confirmed else 'mapping_review'
    elif mapping_status=='matched' and scope_confirmed: state='eligible_for_existing_admission'
    else: state='scope_review'
    return {**binding,'workflow_state':state,'preserve_source_record':True,
            'excluded_from_main_graph':state=='excluded_by_scope'}
