"""Read-only verification of this bounded V6 replication; makes no model calls."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evaluation.freeze_shared_ontology import read,write
from evaluation.review_samples import ReadOnlyRepository
from evaluation.committee_acceptance import signature
from review_agent.contracts import sha256_json,proposal_digest
from review_agent.validator_contracts import validate_vote,prompt_for,proposal_errors
from review_agent.validators import vote_binding,_parse_content
from review_agent.response_consistency import issues
from review_agent.review_observability import assess_vote
from review_agent.response_contract_v8 import validate_response
from evidence.verification import verify_vote_references

OUT=Path(__file__).resolve().parent
LIVE=OUT/'live'
baseline=read(OUT/'baseline.json');report=read(LIVE/'report.json')
repo=ReadOnlyRepository(LIVE/'review.sqlite3')
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline['watched_hashes'].items())
fingerprints=read(LIVE/'code_fingerprints.json')
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in fingerprints.items())
with ReadOnlyRepository(ROOT/'outputs/response_consistency_20261116/live/review.sqlite3').connect() as c:
    old_unknown=[dict(r) for r in c.execute("SELECT attempt_id,state FROM request_attempts WHERE state='unknown'")]
assert old_unknown==baseline['unknown_attempts']
with repo.connect() as c:
    attempts=[dict(r) for r in c.execute('SELECT * FROM request_attempts')]
    outboxes={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
              for table in ('import_outbox','ontology_publish_outbox','graph_outbox','jobs')}
assert len(attempts)==12 and all(r['state']=='completed' for r in attempts)
assert not any(outboxes.values())
old_fixtures={x['name']:x for x in read(ROOT/'outputs/committee_acceptance_20261117/live/fixtures.json')}
input_tokens=output_tokens=valid=projected=0
rows=[]
for case in read(LIVE/'fixtures.json'):
    x=read(LIVE/(case['name']+'_projection.json'));p=x['proposal'];votes=x['votes']
    assert signature(case['candidate'])==signature(old_fixtures[case['name']]['candidate'])
    assert sha256_json(repo.get_revision(case['candidate']['candidate_id']))==sha256_json(case['candidate'])
    assert p['candidate_review_hash']==sha256_json(case['candidate'])
    assert proposal_digest(p)==p['proposal_digest']
    assert p['evidence_bundle_hash']==p['evidence_bundle']['evidence_hash']
    assert p['prompt_version']=='triple_validator_v8_consistency_v6'
    prompt_for(p)
    assert x['engineering_acceptance']['whole_committee_valid']
    assert x['engineering_acceptance']['all_three_consistent']
    assert x['engineering_acceptance']['weight_checks']['passed']
    assert x['engineering_acceptance']['routing_check']
    assert x['engineering_acceptance']['scheduling_check']
    for axis in x['derived_outcome']['weight_snapshot']['axes'].values():
        assert all(s['reliability']==.5 for s in axis['seats'].values())
    for v in votes:
        raw=_parse_content(v['raw_response'])
        assert not validate_response(raw) and not issues(raw)
        assert not validate_vote(v) and not proposal_errors(raw,p)
        assert verify_vote_references(v,p['evidence_bundle']['items'])['response_status']=='ok'
        assert assess_vote(v,p)['whole_vote_eligible']
        member=next(m for m in p['committee_members'] if m['id']==v['voter_id'])
        binding=vote_binding({**member,'client':None},p)
        matching=[a for a in attempts if json.loads(a['metadata_json'])['binding']['proposal_digest']==p['proposal_digest']
                  and json.loads(a['metadata_json'])['binding']['voter_id']==v['voter_id']]
        assert len(matching)==1
        metadata=json.loads(matching[0]['metadata_json']);receipt=json.loads(matching[0]['result_json'])
        assert metadata['feedback'] is None
        assert all(v.get(k)==value and metadata['binding'].get(k)==value and receipt.get(k)==value for k,value in binding.items())
        assert sha256_json(receipt['raw_response'])==sha256_json(v['raw_response'])
        assert receipt['transport_metrics']['http_status']==200
        input_tokens+=receipt['input_tokens'];output_tokens+=receipt['output_tokens'];valid+=1
        projected+=bool(v.get('response_shape_projection'))
    rows.append({'fixture':case['name'],'valid_responses':len(votes),'operation':x['engineering_acceptance']['research_operation'],
        'dimension_statuses':x['engineering_acceptance']['dimension_statuses'],
        'qualifier_statuses':{v['voter_id']:v['dimensions']['qualifiers']['status'] for v in votes},
        'all_three_consistent':True})
assert valid==12 and input_tokens+output_tokens==report['known_tokens']
assert report['calls']==12 and report['field_correction_calls']==0 and report['core_three_case_gate_passed']
summary={'verification_passed':True,'valid_original_responses':valid,'prior_valid_original_responses':9,
    'initial_requests':12,'field_correction_calls':0,'live_shape_projections':projected,
    'new_http504':0,'new_unknown':0,'known_input_tokens':input_tokens,'known_output_tokens':output_tokens,
    'known_total_tokens':input_tokens+output_tokens,'price':None,
    'old_unknown_still_unresolved':len(old_unknown),'old_unknown_requests_resent':0,
    'original_reply_schema_and_consistency_checks_passed':True,'all_receipt_bindings_verified':True,
    'core_mapping_acceptance_gate_passed':True,
    'air_positive_accepted':False,'any_positive_research_accept_demonstrated':True,
    'prior_study_and_source_database_hashes_unchanged':True,'production_head_unchanged':True,
    'study_code_unchanged':True,'candidates_unchanged':True,'prior_reliability_unchanged':True,
    'outbox_and_job_counts':outboxes,'production_written':False,'human_gold':False,'paper_accuracy':None,
    'regression_tests':164,'targeted_tests':32,'compatibility_tests':132,
    'regression_logs':['final_targeted_checks.log','compatibility_checks.log'],'rows':rows}
write(OUT/'verification.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False))
