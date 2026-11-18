"""Versioned response construction rules and read-only inconsistency diagnostics."""
from copy import deepcopy
from .contracts import sha256_json

LEGACY_PROMPT_VERSION='triple_validator_v8_citations_v5'
PROMPT_VERSION='triple_validator_v8_consistency_v6'
LEGACY_VERSION='original_candidate_response_consistency_v1'
VERSION='original_candidate_response_consistency_v2'


def policy(version=None):
    from .response_contract_v8 import RESPONSE_SCHEMA
    version=version or VERSION
    if version not in {LEGACY_VERSION,VERSION}:raise ValueError('unsupported response consistency policy')
    result={'version':version,'evidence_role_target':'exact_original_candidate_full_expression',
        'expression_checks':deepcopy(RESPONSE_SCHEMA['properties']['expression_findings']['items']['properties']['check']['enum']),
        'rules':[
            'Assign support/counter/context/irrelevant relative to the ORIGINAL candidate, including its exact surface predicate, not a corrected candidate or a true sentence considered alone.',
            'A positive source statement may contradict the original candidate. If triple_support=contradicted, at least one actual evidence_assessment must have role=counter. Do not label that evidence support merely because it proves a proposed correction.',
            'primary_evidence_ids contains only role=support IDs and never counter IDs. It may be empty when the original candidate is contradicted; a correction needs a separate reviewed revision.',
            'dimensions.counterevidence.evidence_ids cites material inspected for contradiction. counterevidence_reviewed_ids instead records dismissed potential counterexamples; it never includes unresolved role=counter IDs.',
            'Actual unresolved role=counter evidence requires counterevidence=fail/unknown, never pass. Without an actual contradiction use insufficient/unknown where appropriate; do not fabricate counterevidence for consistency.',
            'expression_findings.check uses ONLY expression_checks. Mapping findings belong in dimensions.mapping; do not add mapping/ontology/counterevidence as expression finding checks.',
            'Complete every required mapping field: status, relation_id, competing_relation_ids, evidence_ids and note. A known ID and compatible types alone do not establish equivalence of the exact surface predicate to that property.',
            'Check consistency of the independently reached statuses, roles and findings BEFORE sending. Do not alter substantive judgments merely to pass validation. Return no additional top-level fields and no admission decision.'
        ]}
    if version==VERSION:
        result['rules'] += [
            'Construct dimensions.mapping as ONE nested object with relation_id and competing_relation_ids inside it; those keys are never siblings of mapping.',
            'For each expression check, independently decide its status and findings as one unit: unresolved findings require that same dimension status=unknown; semantic_error findings require status=fail. A pass cannot coexist with an unresolved or semantic_error finding for that check.',
            'If modality remains genuinely uncertain after reading the sentence and formula, keep the qualifier check unknown and a matching unresolved MOD-001 finding. Never automatically replace can with asserted or clear the finding to achieve a pass.',
            'Before sending, build the actual counter-role set. If it is nonempty, counterevidence.status cannot be pass; retain fail/unknown according to the evidence. A completed search is not a no-contradiction judgment.'
        ]
    return result


def frozen(proposal):
    prompt=proposal.get('prompt_version')
    if prompt not in {LEGACY_PROMPT_VERSION,PROMPT_VERSION}:return None
    expected=policy(LEGACY_VERSION if prompt==LEGACY_PROMPT_VERSION else VERSION)
    value=proposal.get('response_consistency_policy')
    if proposal.get('response_consistency_binding_version') not in {None,'response_consistency_digest_v1'}:
        raise ValueError('unsupported response consistency binding version')
    if value!=expected or sha256_json(value)!=proposal.get('response_consistency_policy_hash'):
        raise ValueError('frozen response consistency policy mismatch')
    return deepcopy(value)


def issues(raw):
    """Diagnostics never fill references, reassign evidence, or change opinions."""
    if not isinstance(raw,dict):return []
    dims=raw.get('dimensions');dims=dims if isinstance(dims,dict) else {}
    assessments=raw.get('evidence_assessments');assessments=assessments if isinstance(assessments,list) else []
    role_ids={role:{a['evidence_id'] for a in assessments if isinstance(a,dict)
                   and isinstance(a.get('evidence_id'),str) and a.get('role')==role}
              for role in ('support','counter')}
    def ids(key):
        value=raw.get(key);return {v for v in value if isinstance(v,str)} if isinstance(value,list) else set()
    def status(key):return dims[key].get('status') if isinstance(dims.get(key),dict) else None
    result=[]
    def add(code,path,detail):result.append({'code':code,'path':path,'detail':detail,'automatic_repair':False})
    if status('triple_support')=='contradicted' and not role_ids['counter']:
        add('CONTRADICTION_WITHOUT_COUNTER','/evidence_assessments','A contradicted original candidate needs actual counter-role evidence; reassignment requires a semantic judgment.')
    if role_ids['support']&role_ids['counter']:
        add('CONFLICTING_EVIDENCE_ROLES','/evidence_assessments','The same evidence cannot support and contradict the same original expression.')
    if ids('primary_evidence_ids')-role_ids['support']:
        add('PRIMARY_ROLE_MISMATCH','/primary_evidence_ids','Positive primary references must already have support roles.')
    if ids('counterevidence_reviewed_ids')&role_ids['counter']:
        add('COUNTER_DISMISSED_OVERLAP','/counterevidence_reviewed_ids','An unresolved material contradiction cannot also be dismissed.')
    if status('counterevidence')=='pass' and role_ids['counter']:
        add('COUNTER_STATUS_CONFLICT','/dimensions/counterevidence/status','No-unresolved-contradiction conflicts with actual counter-role evidence.')
    findings=raw.get('expression_findings');findings=findings if isinstance(findings,list) else []
    allowed=policy()['expression_checks']
    for index,finding in enumerate(findings):
        if isinstance(finding,dict) and finding.get('check') not in allowed:
            add('EXPRESSION_CHECK_OUTSIDE_CONTRACT',f'/expression_findings/{index}/check','Mapping and other non-expression checks belong in their own dimensions; findings cannot be silently dropped or renamed.')
        if isinstance(finding,dict) and finding.get('check') in allowed:
            expected={'unresolved':'unknown','semantic_error':'fail'}.get(finding.get('kind'))
            if expected and status(finding['check'])!=expected:
                add('FINDING_STATUS_CONFLICT',f'/dimensions/{finding["check"]}/status',
                    f'{finding["kind"]} finding requires {expected}; existing uncertainty or contradiction cannot be silently removed.')
    for key in ('relation_id','competing_relation_ids'):
        if key in dims:add('MAPPING_FIELD_WRONG_LEVEL','/dimensions/'+key,'Mapping reference fields must be nested in dimensions.mapping; only a frozen lossless shape policy can move them.')
    return result
