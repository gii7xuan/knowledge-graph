"""Remove duplicate diagnostic bodies from the unsubmitted B01 regression context."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from review_agent.repository import ReviewRepository
from review_agent.contracts import sha256_json
from ontology_review.contracts import proposal_digest
from ontology_review.review_payload import validator_payload
from ontology_review.semantic_competency import request_for


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def digest(path): return __import__('hashlib').sha256(path.read_bytes()).hexdigest()


def main():
    if (OUT / 'execution_started.json').exists() or (OUT / 'snapshot_round2').exists(): raise ValueError('Do not rewrite a started or already-refined review')
    m = read(OUT / 'prepared_manifest.json'); p = read(OUT / 'prepared_b01_proposal.json')
    assert p['round_id'] == m['round_id'] == 2
    repo = ReviewRepository(m['database'])
    audit = repo.ontology_audit(p['audit_id'])
    assert not any(v['round_id'] == 2 for v in audit['votes'])
    previous = OUT / 'snapshot_round2'; previous.mkdir()
    for name in ('prepared_manifest.json','prepared_b01_proposal.json','prepared_b01_modeling_payload.json','prepared_b01_competency_request.json','review_preflight.json'):
        shutil.copy2(OUT / name, previous / name)
    full = read(OUT / 'structural_regression_record.json')
    rows = read(OUT / 'offline_results.json')['observations']
    compact_cases = []
    for case in full['cases']:
        compact_cases.append({k: deepcopy(case[k]) for k in ('case_id','target_revision','polarity','candidate','semantic_note')} | {
            'source_evidence_ids': [e['evidence_id'] for e in case['evidence']],
            'expected_named_constraints': {k: case['expected'][k] for k in ('baseline','B01')},
            'observed_named_constraints': {schema: {k: next(r for r in rows if r['case_id']==case['case_id'] and r['schema']==schema)['observed_guard'][k]
                for k in ('status','domain_status','range_status')} for schema in ('baseline','B01')}})
    record = {k: deepcopy(v) for k,v in full.items() if k not in ('cases','results','record_id')}
    record.update(cases=compact_cases, source_quotes_from='proposal.evidence by source_evidence_ids',
        local_full_record_sha256=digest(OUT / 'structural_regression_record.json'),
        delivery='complete_fixture_triples_and_expected_observed_named_constraints_without_duplicate_headers_or_guard_metadata')
    record['record_id'] = 'check_' + sha256_json(['targeted_structural_regression_compact_v1',record])
    fresh = deepcopy(p); fresh['round_id'] = 3
    fresh['check_records']['regression'] = record
    fresh['proposal_digest'] = proposal_digest(fresh)
    assert fresh['operations'] == p['operations'] and fresh['source_patch'] == p['source_patch'] and fresh['evidence'] == p['evidence']
    repo.ontology_proposal(fresh)
    request, _ = request_for(repo, Path(m['release_root']), fresh)
    write(OUT / 'modeling_regression_record.json', record)
    write(OUT / 'prepared_b01_proposal.json', fresh)
    write(OUT / 'prepared_b01_modeling_payload.json', validator_payload(fresh, delivery_version='ontology_validator_payload_v3'))
    write(OUT / 'prepared_b01_competency_request.json', request)
    grant = read(OUT / 'authorization_request.json'); grant['proposal_digest'] = fresh['proposal_digest']
    write(OUT / 'authorization_request.json', grant)
    m.update(round_id=3, proposal_digest=fresh['proposal_digest'],
        pre_dispatch_refinement={'previous_round':2,'reason':'Remove duplicate quotation and transport/guard bodies; retain every fixture, source ID, triple and expected/observed constraint judgment.',
                                'refinement_script_sha256':digest(Path(__file__))})
    m['artifact_hashes'] = {name: digest(OUT/name) for name in m['artifact_hashes']}
    m['artifact_hashes']['modeling_regression_record.json'] = digest(OUT/'modeling_regression_record.json')
    write(OUT/'prepared_manifest.json',m)
    print(json.dumps({'round_id':3,'proposal_digest':fresh['proposal_digest'],'regression_context_bytes_before':len(json.dumps(full,ensure_ascii=False).encode()),
        'regression_context_bytes_after':len(json.dumps(record,ensure_ascii=False).encode()),'real_model_calls':0},ensure_ascii=False))


if __name__=='__main__': main()
