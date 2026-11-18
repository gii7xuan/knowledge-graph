"""Versioned lossless moves of two unambiguously misplaced mapping fields."""
from copy import deepcopy
from .contracts import sha256_json

PROMPT_VERSION='triple_validator_v8_consistency_v6'
VERSION='mapping_field_shape_projection_v1'
FIELDS=('relation_id','competing_relation_ids')


def policy():
    return {'version':VERSION,'moves':[{'from':'/dimensions/'+k,'to':'/dimensions/mapping/'+k} for k in FIELDS],
        'target_must_be_absent':True,'values_types_and_judgments':'unchanged','unknown_fields':'reject',
        'semantic_conflicts':'retain_invalid','raw_response':'immutable','automatic_model_calls':0}


def frozen(proposal):
    if proposal.get('prompt_version')!=PROMPT_VERSION:return None
    value=proposal.get('response_projection_policy')
    if value!=policy() or sha256_json(value)!=proposal.get('response_projection_policy_hash'):
        raise ValueError('frozen response projection policy mismatch')
    return deepcopy(value)


def apply(raw):
    """Return a new view; never choose values, merge conflicts, or drop opinions."""
    from .response_contract_v8 import RESPONSE_SCHEMA
    from .schema_validation import schema_errors
    result=deepcopy(raw)
    if not isinstance(result,dict) or not isinstance(result.get('dimensions'),dict):return result,[]
    dims=result['dimensions'];mapping=dims.get('mapping')
    if not isinstance(mapping,dict):return result,[]
    extras=set(dims)-set(RESPONSE_SCHEMA['properties']['dimensions']['properties'])
    if not extras or not extras<=set(FIELDS):return result,[]
    for key in extras:
        if key in mapping or schema_errors(dims[key],RESPONSE_SCHEMA['properties']['dimensions']['properties']['mapping']['properties'][key]):
            return result,[]
    moves=[]
    for key in FIELDS:
        if key not in extras:continue
        value=dims.pop(key);mapping[key]=value
        moves.append({'from':'/dimensions/'+key,'to':'/dimensions/mapping/'+key,'value_hash':sha256_json(value)})
    return result,moves


def project(raw,proposal):
    p=frozen(proposal)
    if p is None:return deepcopy(raw),None
    projected,moves=apply(raw)
    if not moves:return projected,None
    trace={'version':VERSION,'policy':p,'policy_hash':sha256_json(p),'proposal_digest':proposal['proposal_digest'],
        'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'original_response_hash':sha256_json(raw),
        'projected_response_hash':sha256_json(projected),'moves':moves,'semantic_judgments_changed':False}
    return projected,{'response_shape_projection':trace,'shape_projected_response':projected}


def validate(vote,proposal=None):
    trace=vote.get('response_shape_projection')
    if trace is None:
        if vote.get('shape_projected_response') is not None:return ['projected response has no projection trace']
        if vote.get('prompt_version')==PROMPT_VERSION and vote.get('response_status')=='ok' and vote.get('response_origin')!='field_patch_merge':
            try:
                from .validators import _parse_content
                from .response_contract_v6 import canonicalize
                from .response_contract_v8 import raw_from_vote
                raw=_parse_content(vote['raw_response']);_,moves=apply(raw)
                if moves:return ['required response shape projection trace missing']
                if canonicalize(raw)[0]!=canonicalize(raw_from_vote(vote))[0]:return ['stored model fields differ from original response']
            except (ValueError,KeyError,TypeError):return ['complete original response required for V6 provenance']
        return []
    try:
        from .validators import _parse_content
        from .response_contract_v6 import canonicalize
        from .response_contract_v8 import raw_from_vote
        raw=_parse_content(vote['raw_response']);projected,moves=apply(raw)
        bound=vote.get('cache_source',{}).get('proposal_digest',vote.get('proposal_digest'))
        if (vote.get('prompt_version')!=PROMPT_VERSION or trace.get('version')!=VERSION or trace.get('policy')!=policy()
                or trace.get('policy_hash')!=sha256_json(policy()) or vote.get('response_projection_policy_hash')!=sha256_json(policy())
                or trace.get('proposal_digest')!=bound or trace.get('evidence_hash')!=vote.get('evidence_hash')
                or not moves or trace.get('moves')!=moves or trace.get('semantic_judgments_changed') is not False
                or trace.get('original_response_hash')!=sha256_json(raw) or trace.get('projected_response_hash')!=sha256_json(projected)
                or vote.get('shape_projected_response')!=projected):
            return ['invalid response shape projection provenance']
        if proposal is not None:
            if frozen(proposal)!=trace['policy']:return ['response projection differs from frozen request']
        recorded=raw_from_vote(vote)
        if recorded and canonicalize(recorded)[0]!=canonicalize(projected)[0]:return ['projection differs from stored model fields']
    except (ValueError,KeyError,TypeError,AttributeError):return ['invalid response shape projection trace']
    return []


def effective_raw(vote,proposal=None):
    errors=validate(vote,proposal)
    if errors:raise ValueError('; '.join(errors))
    return deepcopy(vote['shape_projected_response'])
