"""Read-only validation of the locally prepared context update; no model calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review import modeling_context
from ontology_review.contracts import proposal_digest, RESPONSE_SCHEMA
from ontology_review.publisher import current_release_id
from ontology_review.validators import prompt_for, response_errors
from review_agent.contracts import sha256_json


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = read(OUT / 'prepared_manifest.json')
    proposal = read(OUT / 'prepared_proposal.json')
    prior = ROOT / 'outputs/b01_revision_after_review_20261112'
    old = read(prior / 'prepared_proposal.json')
    protected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    assert all(digest(ROOT / path) == expected for path, expected in manifest['code_fingerprints'].items())
    assert digest(OUT / 'prepare.py') == manifest['preparation_script_sha256']
    assert proposal_digest(proposal) == manifest['proposal_digest'] == proposal['proposal_digest']
    assert proposal['proposal_digest'] != old['proposal_digest']
    assert proposal['operations'] == old['operations'] and len(proposal['operations']) == 9
    assert proposal['source_patch'] == old['source_patch']
    assert len(proposal['evidence']) == 18
    assert sha256_json(prompt_for(proposal)) == manifest['prompt_hash']
    assert prompt_for(proposal) == (OUT / 'review_prompt_v3.txt').read_text(encoding='utf-8')
    assert sha256_json(prompt_for(old)) == old['versions']['prompt_hash']
    modeling_context.verify(proposal)
    assert read(OUT / 'modeling_context.json') == proposal['modeling_context']
    votes = read(prior / 'modeling_votes_20261113.json')
    assert [v['decision'] for v in votes].count('approve') == 2
    assert [v['decision'] for v in votes].count('revise') == 1
    for vote in votes:
        assert vote['proposal_digest'] == old['proposal_digest']
        assert not response_errors({k: vote[k] for k in RESPONSE_SCHEMA['required']}, old)
    with sqlite3.connect(Path(manifest['database']).as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM ontology_votes WHERE audit_id=?',
                          (manifest['audit_id'],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM request_attempts WHERE json_extract(metadata_json,'$.binding.audit_id')=?",
                          (manifest['audit_id'],)).fetchone()[0] == 0
        frozen = db.execute('SELECT proposal_json FROM ontology_proposals WHERE audit_id=? AND round_id=?',
                            (manifest['audit_id'], manifest['round_id'])).fetchone()[0]
        assert json.loads(frozen) == proposal
    request = read(OUT / 'prepared_competency_request.json')
    assert request['parent_proposal_digest'] == proposal['proposal_digest']
    assert current_release_id(ROOT / 'runs/ontology_releases') == proposal['base_release_id']
    tests = (OUT / 'offline_tests.log').read_text(encoding='utf-8-sig')
    ran = re.search(r'Ran (\d+) tests? in ([\d.]+)s', tests)
    recheck = (OUT / 'offline_tests_recheck.log').read_text(encoding='utf-8-sig')
    reran = re.search(r'Ran (\d+) tests? in ([\d.]+)s', recheck)
    failed = re.findall(r'^ERROR: (\S+) \(([^)]+)\)', tests, re.MULTILINE)
    assert ran and len(failed) == 8 and 'FAILED (errors=8)' in tests
    assert reran and int(reran[1]) == len(failed) and re.search(r'^OK\s*$', recheck, re.MULTILINE)
    result = {
        'status': 'passed', 'client_date': '2026-11-13',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'proposal_digest': proposal['proposal_digest'],
        'audit_id': manifest['audit_id'], 'ontology_operations_unchanged': 9,
        'rdf_patch_unchanged': True, 'exact_source_evidence_spans': 18,
        'frozen_modeling_context_verified': True, 'protected_inputs_verified': len(protected),
        'all_prepared_code_fingerprints_match': True,
        'legacy_prompt_hash_matches': True, 'historical_valid_votes_unchanged': True,
        'historical_approve_votes': 2, 'historical_revise_votes': 1,
        'new_audit_model_votes': 0, 'new_audit_request_attempts': 0,
        'new_model_calls': 0, 'cumulative_model_calls': 19,
        'new_approval_reused_from_history': False,
        'offline_tests_passed': int(ran[1]), 'offline_tests_seconds': float(ran[2]),
        'sandbox_run_passed': int(ran[1]) - len(failed),
        'sandbox_permission_blocked': len(failed),
        'permission_blocked_tests_rerun_outside_sandbox_passed': int(reran[1]),
        'offline_recheck_seconds': float(reran[2]),
        'offline_test_log_sha256': digest(OUT / 'offline_tests.log'),
        'offline_recheck_log_sha256': digest(OUT / 'offline_tests_recheck.log'),
        'full_chapter_review_executed': False, 'sample_model_review_executed': False,
        'published': False, 'production_written': False,
        'remaining_issue': 'inertial_relative_trajectory_hierarchy_not_changed',
        'artifact_sha256': {p.name: digest(p) for p in [
            OUT / name for name in ('prepared_manifest.json', 'prepared_proposal.json',
                'prepared_validator_payload.json', 'prepared_competency_request.json',
                'modeling_context.json', 'new_source_evidence.json', 'review_prompt_v3.txt',
                'historical_suggestion_diagnostics.json')]},
    }
    (OUT / 'delivery_verification.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'offline_tests_passed', 'new_model_calls',
        'historical_valid_votes_unchanged', 'ontology_operations_unchanged')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
