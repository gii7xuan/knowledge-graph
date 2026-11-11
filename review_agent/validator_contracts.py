"""Explicit version dispatch; historical contracts are never silently corrected."""
from .contracts import sha256_json


def contract_for(version=None):
    from . import response_contract
    if version in (None,response_contract.CONTRACT_VERSION): return response_contract
    from . import response_contract_v4
    if version==response_contract_v4.CONTRACT_VERSION: return response_contract_v4
    from . import response_contract_v5
    if version==response_contract_v5.CONTRACT_VERSION: return response_contract_v5
    from . import response_contract_v5_1
    if version==response_contract_v5_1.CONTRACT_VERSION: return response_contract_v5_1
    from . import response_contract_v5_2
    if version==response_contract_v5_2.CONTRACT_VERSION: return response_contract_v5_2
    from . import response_contract_v6
    if version==response_contract_v6.CONTRACT_VERSION: return response_contract_v6
    from . import response_contract_v6_1
    if version==response_contract_v6_1.CONTRACT_VERSION: return response_contract_v6_1
    from . import response_contract_v6_2
    if version==response_contract_v6_2.CONTRACT_VERSION: return response_contract_v6_2
    from . import response_contract_v6_3
    if version==response_contract_v6_3.CONTRACT_VERSION: return response_contract_v6_3
    from . import response_contract_v7
    if version==response_contract_v7.CONTRACT_VERSION: return response_contract_v7
    from . import response_contract_v8
    if version==response_contract_v8.CONTRACT_VERSION: return response_contract_v8
    raise ValueError('unsupported validator response contract: '+str(version))


def versions_for(config):
    contract=contract_for(config.get('validator_contract_version'))
    correction={}
    if getattr(contract,'TARGETED_REPAIRS',False):
        from .response_corrections import policy_for
        policy=policy_for(config)
        correction={'correction_policy':policy,'correction_policy_version':policy['version'],'correction_policy_hash':sha256_json(policy)}
    ontology={}
    if getattr(contract,'DYNAMIC_ONTOLOGY',False):
        from .ontology_context import policy_for
        policy=policy_for(config)
        ontology={'ontology_context_policy':policy,'ontology_context_policy_version':policy['version'],
                  'ontology_context_policy_hash':sha256_json(policy)}
    mapping = {}
    if contract.CONTRACT_VERSION in {'validator_response_v7','validator_response_v8'} and (config.get('mapping_preflight') or {}).get('enabled'):
        from .mapping_preflight import policy_for
        policy = policy_for(config)
        mapping = {'mapping_preflight_policy': policy, 'mapping_preflight_policy_hash': sha256_json(policy)}
    fields = {}
    if contract.CONTRACT_VERSION == 'validator_response_v8':
        from .field_spec import specification
        spec = specification()
        fields = {'field_spec':spec,'field_spec_version':spec['version'],'field_spec_hash':sha256_json(spec)}
        if (config.get('dispute_review') or {}).get('enabled'):
            from .disputes import policy_for
            policy=policy_for(config)
            fields.update(dispute_policy=policy,dispute_policy_hash=sha256_json(policy))
    return {'response_contract_version':contract.CONTRACT_VERSION,'response_contract_hash':sha256_json(contract.RESPONSE_SCHEMA),
            'prompt_version':contract.PROMPT_VERSION,'prompt_hash':contract.PROMPT_HASH,
            **({'evidence_bundle_version':'evidence_bundle_v3'} if contract.CONTRACT_VERSION in {'validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'} else {}),
            **({'summary_policy_version':contract.SUMMARY_POLICY_VERSION,'summary_policy_hash':sha256_json(contract.SUMMARY_POLICY)}
               if hasattr(contract,'SUMMARY_POLICY') else {}),
            **({'output_delivery_version':contract.OUTPUT_DELIVERY_VERSION,'output_delivery_hash':sha256_json(contract.OUTPUT_DELIVERY_POLICY)}
               if hasattr(contract,'OUTPUT_DELIVERY_POLICY') else {}),**correction,**ontology,**mapping,**fields}


def prompt_for(proposal):
    """Frozen v4 tickets retain their original prompt and request identity."""
    from pathlib import Path
    import hashlib
    contract = contract_for(proposal.get('response_contract_version'))
    if 'review_scope_policy' in proposal:
        from .scope_policy import validate_policy
        validate_policy(proposal['review_scope_policy'])
    if getattr(contract,'DYNAMIC_ONTOLOGY',False):
        from .ontology_context import frozen_context
        frozen_context(proposal)
    if 'mapping_preflight_policy' in proposal:
        from .mapping_preflight import frozen
        frozen(proposal)
    if proposal.get('response_contract_version') == 'validator_response_v8':
        from .field_spec import frozen
        frozen(proposal)
    if 'dispute_policy' in proposal:
        from .disputes import frozen
        frozen(proposal)
    if getattr(contract,'TARGETED_REPAIRS',False):
        from .response_corrections import POLICY_VERSION,PROMPT_HASH
        policy=proposal.get('correction_policy') or {}
        if policy.get('version') not in {'targeted_review_repairs_v1',POLICY_VERSION} or policy.get('prompt_hash')!=PROMPT_HASH or sha256_json(policy)!=proposal.get('correction_policy_hash'):
            raise ValueError('frozen field correction policy mismatch')
    if hasattr(contract,'OUTPUT_DELIVERY_POLICY') and (proposal.get('output_delivery_version')!=contract.OUTPUT_DELIVERY_VERSION
            or proposal.get('output_delivery_hash')!=sha256_json(contract.OUTPUT_DELIVERY_POLICY)):
        raise ValueError('frozen output delivery hash/version mismatch')
    if hasattr(contract,'SUMMARY_POLICY') and (proposal.get('summary_policy_version')!=contract.SUMMARY_POLICY_VERSION
            or proposal.get('summary_policy_hash')!=sha256_json(contract.SUMMARY_POLICY)):
        raise ValueError('frozen summary policy hash/version mismatch')
    version = proposal.get('prompt_version')
    if contract.CONTRACT_VERSION == 'validator_response_v4' and version == 'triple_validator_v4':
        prompt = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v4_legacy.txt').read_text(encoding='utf-8')
    elif contract.CONTRACT_VERSION == 'validator_response_v8' and version == 'triple_validator_v8_field_rules_v1':
        prompt = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v8.txt').read_text(encoding='utf-8')
    elif contract.CONTRACT_VERSION == 'validator_response_v8' and version == 'triple_validator_v8_citations_v2':
        prompt = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v8_citations_v2.txt').read_text(encoding='utf-8')
    elif contract.CONTRACT_VERSION == 'validator_response_v8' and version == 'triple_validator_v8_citations_v3':
        prompt = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v8_citations_v3.txt').read_text(encoding='utf-8')
    else:
        prompt = contract.SYSTEM_PROMPT
    if contract.CONTRACT_VERSION == 'validator_response_v8' and version not in {'triple_validator_v8_field_rules_v1','triple_validator_v8_citations_v2','triple_validator_v8_citations_v3','triple_validator_v8_citations_v4'}:
        raise ValueError('unsupported frozen V8 prompt version')
    if version in {'triple_validator_v4', 'triple_validator_v4_direction_scope_v1', 'triple_validator_v5_semantic_units_v1', 'triple_validator_v5_1_complete_output_v1', 'triple_validator_v5_2_auxiliary_format_v1', 'triple_validator_v6_dimensions_v1', 'triple_validator_v6_1_structured_output_v1', 'triple_validator_v6_2_targeted_repairs_v1', 'triple_validator_v6_3_dynamic_ontology_v1','triple_validator_v7_four_axes_v1','triple_validator_v8_field_rules_v1','triple_validator_v8_citations_v2','triple_validator_v8_citations_v3','triple_validator_v8_citations_v4'} and proposal.get('prompt_hash'):
        if hashlib.sha256(prompt.encode()).hexdigest() != proposal['prompt_hash']:
            raise ValueError('frozen source prompt hash mismatch')
    return prompt


def validate_vote(vote,version=None):
    contract=contract_for(version or vote.get('response_contract_version'))
    raw=contract.raw_from_vote(vote) if hasattr(contract,'raw_from_vote') else {k:vote[k] for k in contract.RESPONSE_SCHEMA['properties'] if k in vote}
    errors=contract.validate_response(raw)
    if not errors and hasattr(contract,'validate_correction_trace'):errors+=contract.validate_correction_trace(vote)
    if not errors and hasattr(contract,'normalize'):
        normalized=contract.normalize(raw)
        fields=getattr(contract,'NORMALIZED_COMPARE_FIELDS',('schema_status','schema_issue','checks'))
        if any(vote.get(k)!=normalized.get(k) for k in fields):
            errors.append('server-normalized schema assessment mismatch')
    return errors


def proposal_errors(value,proposal):
    if proposal.get('response_contract_version') == 'validator_response_v8':
        from .field_spec import finding_errors
        errors = finding_errors(value,proposal)
        if errors: return errors
    elif proposal.get('response_contract_version') == 'validator_response_v7':
        from .review_axes import normalization_errors
        errors = normalization_errors(value, proposal)
        if errors: return errors
    return mapping_reference_errors(value,proposal)


def mapping_reference_errors(value,proposal):
    """Formal references can be checked without inventing other dimension fields."""
    if proposal.get('response_contract_version') in {'validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}:
        # A model's semantic opinion is valid even when the server cannot admit it.
        # Only references to nonexistent formal properties are protocol errors.
        if proposal.get('response_contract_version')in {'validator_response_v6_3','validator_response_v7','validator_response_v8'}:
            from .ontology_context import frozen_context
            catalog=frozen_context(proposal)
        else:
            catalog=proposal.get('ontology_catalog')
        mapping=((value.get('dimensions') or {}).get('mapping') if proposal.get('response_contract_version') in {'validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'} else value.get('mapping')) or {}
        if catalog is not None:
            known={p.get('id') for p in catalog.get('properties',[])}
            referenced=set(mapping.get('competing_relation_ids') or [])
            if mapping.get('relation_id'): referenced.add(mapping['relation_id'])
            if referenced-known:
                prefix=('mapping references relation IDs outside frozen ontology context: '
                        if proposal.get('response_contract_version')in {'validator_response_v6_3','validator_response_v7','validator_response_v8'}
                        else 'mapping references unknown formal relation IDs: ')
                return [prefix+str(sorted(referenced-known))]
        return []
    if proposal.get('response_contract_version')!='validator_response_v5': return []
    mapping=value.get('mapping') or {}
    assigned=(proposal.get('candidate') or {}).get('triple',{}).get('relation_id')
    if mapping.get('status')=='matched' and mapping.get('relation_id')!=assigned:
        return ['mapping matched requires the current formal relation_id; propose a new revision instead']
    catalog=proposal.get('ontology_catalog')
    if mapping.get('status')=='matched' and catalog is not None:
        prop=next((p for p in catalog.get('properties',[]) if p.get('id')==assigned),None)
        if not prop or not (prop.get('definition') or prop.get('description') or prop.get('definitions')):
            return ['formal relation definition unavailable: keep mapping unresolved, not matched']
        if proposal.get('ontology_check',{}).get('status')!='pass':
            return ['matched mapping requires confirmed endpoint types and compatible constraints']
    return []
