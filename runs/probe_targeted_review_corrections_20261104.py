"""One synthetic field patch per authorized endpoint; never a chapter review."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.output_structure import resolve_profile
from review_agent.progress import write_status_snapshot
from review_agent.service import load_config
from review_agent.validator_contracts import versions_for
from review_agent.validators import _bounded_attempt
from review_agent import response_contract_v6_2 as contract,response_corrections as repairs

OUT=Path(__file__).resolve().parents[1]/'outputs/targeted_review_corrections_20261104'


def probe(voter,config):
    path=OUT/('probe_'+voter['id']+'.json')
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    original={'dimensions':{n:{'status':'unknown','evidence_ids':[],'note':'synthetic no source material','issue_code':None} for n in contract.CHECK_NAMES if n!='mapping'},
        'evidence_assessments':[],'primary_evidence_ids':[],'counterevidence_reviewed_ids':[],'requested_context':None,'suggested_revision':None}
    original['dimensions']['triple_support']['status']='insufficient';original['dimensions']['knowledge_scope']['status']='unresolved'
    original['dimensions']['qualifiers'].update(status='fail',note='synthetic negative opinion must remain unchanged')
    proposal={'audit_id':'synthetic_field_probe','round_id':1,'proposal_digest':'synthetic_field_probe_'+voter['id'],
        **versions_for(config),'candidate':{'candidate_id':'synthetic_no_document','revision':1,
            'triple':{'head':'SyntheticA','surface_relation':'synthetic_relation','tail':'SyntheticB','qualifiers':{}}},
        'evidence_bundle':{'items':[],'evidence_hash':sha256_json([])},'ontology_check':{'status':'unknown','relation_known':False},
        'ontology_catalog':{'properties':[]},'ontology_catalog_hash':sha256_json({'properties':[]})}
    proposal['validator_generation']=[{'voter_id':voter['id'],'max_output_tokens':voter.get('max_output_tokens',8192),
        'output_transport':resolve_profile(voter,config,contract.RESPONSE_SCHEMA)}]
    plan,errors=repairs.make_plan(original,proposal,proposal['correction_policy']);assert plan and not errors
    record={'created_at':datetime.now(timezone.utc).isoformat(),'model':voter['model_id'],'voter_id':voter['id'],
        'state':'dispatching','synthetic_only':True,'document_content_sent':False,'original_review_was_synthetic':True,
        'maximum_provider_requests':1,'automatic_retry':False,'original_response':original,'plan':plan}
    write_status_snapshot(path,record)
    feedback={'kind':'field_patch','repair_plan':plan,'original_response':original,'original_raw_response':deepcopy(original),
        'correction_id':'synthetic_probe','original_attempt_id':'synthetic_fixture'}
    result=_bounded_attempt(voter,proposal,feedback,proposal['correction_policy']['limits']['timeout_seconds'])
    intact=result.get('corrected_response',{}).get('dimensions',{}).get('qualifiers',{}).get('status')=='fail'
    record.update(state='unknown' if result.get('outcome_unknown') else 'completed',response_status=result.get('response_status'),
        original_negative_judgment_preserved=intact,raw_correction_response=result.get('raw_correction_response'),
        correction_result=result,initial_review_requests_sent=0,correction_requests_sent=1)
    write_status_snapshot(path,record);return record


def main():
    OUT.mkdir(parents=True,exist_ok=True);c=load_config();c['validator_contract_version']=contract.CONTRACT_VERSION
    with ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(lambda v:probe(v,c),_configured_validators(c)))
    summary={'records':[{'model':r['model'],'response_status':r['response_status'],'negative_preserved':r['original_negative_judgment_preserved']} for r in records],
        'external_synthetic_requests':3,'document_content_sent':False,'historical_votes_changed':False}
    write_status_snapshot(OUT/'probe_summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
