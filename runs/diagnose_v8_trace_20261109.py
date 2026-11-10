"""Read-only technical diagnosis; no stored vote/decision changes or model calls."""
import json
from pathlib import Path
from unittest.mock import patch
from evaluation.review_samples import ReadOnlyRepository
from review_agent import response_contract_v6_2 as legacy,response_contract_v8 as current
from review_agent.validator_contracts import validate_vote
from review_agent.consensus import Committee,aggregate_votes
from review_agent.progress import write_status_snapshot

OUT=Path(__file__).resolve().parents[1]/'outputs/dev30_v8_rerun_20261109'


def main():
    repo=ReadOnlyRepository(OUT/'review.sqlite3')
    bindings=json.loads((OUT/'sample_bindings.json').read_text(encoding='utf-8'));rows=[]
    for binding in bindings:
        decision=repo.latest_decision(binding['candidate_id']) or {}
        if not decision.get('selected_proposal_digest'):continue
        audit=repo.get_audit(binding['candidate_id'],decision['audit_id'])
        proposal=next(p for p in audit['rounds'] if p['proposal_digest']==decision['selected_proposal_digest'])
        votes=[v for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]
        original_errors={v['voter_id']:validate_vote(v) for v in votes if v['response_status']=='ok'}
        policy=proposal['committee_policy'];committee=Committee([m['id'] for m in proposal['committee_members']],
            policy['required_votes'],policy['minimum_distinct_families'],proposal['committee_version'])
        # Only the reader of the model fields changes inside this process. Every
        # policy hash, source binding, immutable judgment and reference check stays.
        with patch.object(legacy,'raw_from_vote',current.raw_from_vote):
            corrected_errors={v['voter_id']:validate_vote(v) for v in votes if v['response_status']=='ok'}
            diagnostic=aggregate_votes(votes,committee,proposal_digest=proposal['proposal_digest'],proposal=proposal)
        affected=[voter for voter,errors in original_errors.items() if errors==['field correction differs from stored model fields'] and not corrected_errors[voter]]
        rows.append({'sample_id':binding['sample_id'],'audit_id':audit['audit_id'],
            'proposal_digest':proposal['proposal_digest'],'affected_voter_ids':affected,
            'original_eligible_voter_ids':decision['consensus']['eligible_voter_ids'],
            'diagnostic_eligible_voter_ids':diagnostic['eligible_voter_ids'],
            'diagnostic_review_axes':diagnostic.get('review_axes'),'remaining_errors':corrected_errors})
    result={'diagnostic_only':True,'model_calls':0,'historical_votes_changed':False,'stored_decisions_changed':False,
        'admission_authorized':False,'cause':'V6.2 correction trace reads only legacy model fields; V8 expression_findings is excluded from that comparison.',
        'candidate_fix':'Validate merged model fields using the response contract version of the frozen vote.',
        'affected_candidates':sum(bool(r['affected_voter_ids']) for r in rows),
        'affected_votes':sum(len(r['affected_voter_ids']) for r in rows),'cases':rows}
    write_status_snapshot(OUT/'trace_diagnosis.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':main()
