"""Export human/model-identical frozen review packages without first-item quotes."""
import argparse
import csv
import json
import sqlite3
from pathlib import Path
from review_agent.evidence_view import evidence_view
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from ontologyBuild.release_service import load_release
from schema.relation_registry import RelationRegistry


class ReadOnlyRepository(ReviewRepository):
    def connect(self):
        class Connection(sqlite3.Connection):
            def __exit__(self,*args):
                try: return super().__exit__(*args)
                finally: self.close()
        conn=sqlite3.connect(Path(self.database_path).resolve().as_uri()+'?mode=ro',uri=True,factory=Connection)
        conn.row_factory=sqlite3.Row
        return conn


def freeze_review_sample(repository,candidate_id):
    candidate=repository.get_revision(candidate_id)
    decision=repository.latest_decision(candidate_id) or {}
    audit=repository.get_audit(candidate_id,decision.get('audit_id'))
    proposal=next((p for p in audit['rounds'] if p.get('proposal_digest')==decision.get('selected_proposal_digest') and
        p.get('round_id')==decision.get('selected_round_id')),None)
    if not proposal: raise ValueError('sample requires an exact selected proposal; no latest-round fallback')
    if sha256_json(candidate)!=sha256_json(proposal['candidate']): raise ValueError('stale sample proposal')
    votes=[v for v in audit['votes'] if v.get('proposal_digest')==proposal['proposal_digest']]
    packet=evidence_view(proposal['evidence_bundle'],votes,authoritative=decision.get('source_support_result')=='supported')
    return {'candidate':candidate,'binding':{'candidate_id':candidate_id,'revision':candidate['revision'],
        'candidate_content_hash':sha256_json(candidate),'audit_id':audit['audit_id'],'proposal_digest':proposal['proposal_digest'],
        'evidence_hash':packet['evidence_hash'],**{k:candidate.get(k) for k in ('ontology_release_id','profile_hash','source_index_hash')}},
        'review_packet':packet,'votes':votes,'raw_annotation':{},'adjudication_status':'pending'}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--database',required=True)
    p.add_argument('--candidate-ids',nargs='+',required=True);p.add_argument('--output',required=True)
    p.add_argument('--split-role',choices=['development','independent_test'],default='development')
    args=p.parse_args();repo=ReadOnlyRepository(args.database)
    samples=[freeze_review_sample(repo,cid) for cid in args.candidate_ids]
    references={}
    for sample in samples:
        c=sample['candidate']; release=load_release(c['ontology_release_path'])
        references[c['ontology_release_id']]=RelationRegistry(release.ontology).reference_catalog()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    payload={'split_role':args.split_role,'samples':samples,'ontology_references':references,
        'human_and_model_share_evidence_hash':True,'annotation_scope':'raw_and_adjudicated_separate'}
    payload['manifest_hash']=sha256_json(payload)
    with (out/'review_samples.json').open('x',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    with (out/'review_samples.csv').open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['candidate_id','revision','head','relation','tail','main_evidence_status','main_quote','counter_quotes','evidence_hash','full_package'])
        for s in samples:
            t=s['candidate']['triple'];v=s['review_packet'];writer.writerow([s['binding']['candidate_id'],s['binding']['revision'],
                t['head'],t['surface_relation'],t['tail'],v['main_evidence_status'],'\n'.join(x['quote'] for x in v['primary_evidence_items']),
                '\n'.join(x['quote'] for x in v['counter_evidence']),v['evidence_hash'],'review_samples.json'])
    print(json.dumps({'samples':len(samples),'split_role':args.split_role,'manifest_hash':payload['manifest_hash']}))


if __name__=='__main__': main()
