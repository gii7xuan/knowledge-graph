from pathlib import Path
import json
import sqlite3
from document.hierarchy_index import load_document_index
from evidence.verification import verify_evidence_bundle,verify_vote_references
from review_agent.validator_contracts import validate_vote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_quality_checks/chapter1_resume_20261029'
summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
c=sqlite3.connect(Path(summary['snapshot']).as_uri()+'?immutable=1',uri=True)
manifest=json.loads(c.execute('SELECT manifest_json FROM runs WHERE run_id=?',('run_chapter1_20261028_resume1',)).fetchone()[0])
doc=load_document_index(manifest['document_path'],manifest['source_index_hash'],require_verified=True)
fresh=json.loads((ROOT/'runs/review_resume_20261029/fresh_result.json').read_text(encoding='utf-8'))
ids={r['candidate_id'] for r in fresh['results']}
details=json.loads((OUT/'candidate_details.json').read_text(encoding='utf-8'))
rows=[]
for r in details:
    if r['candidate_id'] not in ids: continue
    p=next(p for p in r['proposals'] if p['round_id']==r['decision_record']['selected_round_id'])
    verified=verify_evidence_bundle(p['evidence_bundle'],doc,p['candidate'])
    valid=[v for v in r['selected_votes'] if v['response_status']=='ok']
    rows.append({'candidate_id':r['candidate_id'],'evidence_items':len(p['evidence_bundle']['items']),
        'verified_anchors':len(verified['items']),'rejected_anchors':len(verified['rejected_items']),
        'valid_votes':len(valid),'invalid_references':sum(verify_vote_references(v,verified['items'])['response_status']!='ok' for v in valid),
        'invalid_contract_votes':sum(bool(validate_vote(v,p['response_contract_version'])) for v in valid),
        'binding_mismatches':sum(v.get('task_id')!=r['candidate_id'] or v.get('object_revision')!=r['revision'] or v.get('proposal_digest')!=p['proposal_digest'] for v in valid)})
result={'candidates':len(rows),**{k:sum(r[k] for r in rows) for k in ('evidence_items','verified_anchors','rejected_anchors','valid_votes','invalid_references','invalid_contract_votes','binding_mismatches')},'details':rows}
(OUT/'evidence_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False,indent=2))
c.close()
