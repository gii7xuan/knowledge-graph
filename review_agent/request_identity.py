"""Unknown outcomes follow semantic inputs across credentials and namespaces."""
from .contracts import sha256_json


def semantic_request_key(task_type,voter,proposal,prompt_hash=None):
    client=voter.get('client')
    model=str(voter.get('model_id') or getattr(client,'model_id',''))
    family=str(voter.get('model_family') or getattr(client,'model_family',''))
    identity=str(voter.get('model_identity') or getattr(client,'canonical_model_id',None) or model).casefold()
    if task_type=='triple_review':
        from .validators import validator_payload
        from .validator_contracts import contract_for, prompt_for
        contract=contract_for(proposal.get('response_contract_version'))
        payload=validator_payload(proposal,voter)
        payload.pop('proposal_digest',None)
        if proposal.get('response_contract_version') in {'validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}:
            payload.pop('output_structure',None);payload.pop('output_template',None)
        prompt_hash=sha256_json([prompt_for(proposal),contract.RESPONSE_SCHEMA])
    else:
        payload={k:v for k,v in proposal.items() if k not in {'audit_id','round_id','proposal_digest',
                  'request_cache_namespace','prior_counter_evidence_ids','human_review_context'}}
    return sha256_json(['unknown_request_semantics_v1',task_type,model,family,identity,prompt_hash,payload])
