"""Read-only, zero-call aggregation replay on recorded original model opinions.

No votes are rebound to a new proposal. Validate against the ORIGINAL proposal,
then evaluate alternative policies on those validated observations, explicitly
marked as derived aggregation replays. This never imports labels or writes to DB.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from .review_samples import ReadOnlyRepository
from review_agent.consensus import Committee
from review_agent.contracts import proposal_digest, sha256_json
from review_agent.review_observability import summarize_dimensions
from review_agent.vote_weighting import committee_policy, normalize_policy, operation, weight_report


def replay(proposal, votes, policy=None):
    c=proposal['committee_policy']
    members=[m['id'] for m in proposal['committee_members']]
    if len(members)!=c['committee_size']:raise ValueError('replay needs the original complete frozen roster')
    original=Committee(members,c['required_votes'],c['minimum_distinct_families'],
                       proposal.get('committee_version','committee_v1'),c.get('vote_weighting'))
    if proposal_digest(proposal)!=proposal['proposal_digest']:raise ValueError('original proposal digest mismatch')
    before=sha256_json(votes)
    report=summarize_dimensions(votes,proposal,original)
    results={}
    for mode in ('equal','static','dynamic'):
        p=normalize_policy({**(policy or {}),'mode':mode},len(members))
        committee=Committee(members,c['required_votes'],c['minimum_distinct_families'],original.version,p)
        derived=deepcopy(proposal)
        derived['committee_policy']=committee_policy(committee)
        derived['committee_policy_hash']=sha256_json(derived['committee_policy'])
        derived['proposal_digest']=proposal_digest(derived)
        weighted=weight_report(report,votes,derived,committee)
        blockers=[]
        if proposal['candidate'].get('fact_key_state')=='provisional':blockers.append('candidate_identity_unresolved')
        result={'operation':operation(weighted,blockers=blockers),
                'dimension_results':weighted['dimension_results'],'weight_snapshot':weighted['weight_snapshot'],
                'blockers':blockers,'source_proposal_digest':proposal['proposal_digest'],
                'derived_aggregation_digest':derived['proposal_digest'],'model_calls':0,'new_votes_created':False}
        results[mode]=result
    assert sha256_json(votes)==before
    return {'kind':'aggregation_replay_not_end_to_end','candidate_id':proposal['candidate']['candidate_id'],
            'original_proposal_digest':proposal['proposal_digest'],'original_votes_hash':before,
            'original_validation':report['vote_validation'],'modes':results,
            'dynamic_static_operation_differs':results['dynamic']['operation']!=results['static']['operation']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--limit',type=int,default=3)
    parser.add_argument('--policy',help='JSON weighting configuration, without any truth labels')
    args=parser.parse_args()
    if args.limit<1:raise ValueError('limit must be positive')
    repo=ReadOnlyRepository(args.database)
    with repo.connect() as conn:
        rows=conn.execute('SELECT candidate_id,audit_id FROM audit_runs ORDER BY created_at,audit_id').fetchall()
    cases=[];errors=[]
    for row in rows:
        if len(cases)>=args.limit:break
        audit=repo.get_audit(row['candidate_id'],row['audit_id'])
        if not audit['rounds']:continue
        proposal=audit['rounds'][-1]
        if proposal.get('response_contract_version') not in {'validator_response_v7','validator_response_v8'}:continue
        votes=[v for v in audit['votes'] if v.get('proposal_digest')==proposal['proposal_digest']]
        try:
            policy=json.loads(Path(args.policy).read_text(encoding='utf-8')) if args.policy else None
            cases.append(replay(proposal,votes,policy))
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'candidate_id':row['candidate_id'],'reason':str(exc)})
    if not cases:raise ValueError('no replayable four-axis proposals: '+str(errors))
    summary={'kind':'aggregation_replay_not_end_to_end','source_database':str(Path(args.database).resolve()),
             'cases':len(cases),'model_calls':0,'truth_labels_used':False,'accuracy':None,
             'operations':{mode:dict(Counter(c['modes'][mode]['operation'] for c in cases)) for mode in ('equal','static','dynamic')},
             'dynamic_static_operation_differences':sum(c['dynamic_static_operation_differs'] for c in cases),
             'errors':errors,'records':cases}
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(summary,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in summary.items() if k!='records'},ensure_ascii=False))


if __name__=='__main__':main()
