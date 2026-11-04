from pathlib import Path
import json
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.revision_service import RevisionService

root=Path(__file__).resolve().parents[1]
repo=ReviewRepository(root/'runs/v3/review.sqlite3')
items=[]
for identity in ('cand_466b9468afe0efc3434c','cand_c0f73fb07469b204dbf7','cand_087e253eb4e4af8bcf40','cand_4a0747767cfb9020ae71','cand_ccda7f5e8cf2b462ccab','cand_d05f258f70eb3dfe2220','cand_f9a86d764470a851cd8a','cand_f4876022d0c83d291ac3','cand_8cf5f9e9984ea5fed5f9','cand_5b459021eb0a3fe91145','cand_2cd7690d60efe95c709f'):
    candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
    audit=repo.get_audit(identity,decision['audit_id'])
    try:proposal,_=selected_proposal(repo,candidate,decision)
    except Exception:proposal=None
    archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
    archive.update({e['evidence_id']:e for e in (audit.get('agent_state') or {}).get('state',{}).get('archive',[])})
    with repo.connect() as con:ids=[r[0] for r in con.execute('SELECT draft_id FROM revision_drafts WHERE candidate_id=?',(identity,))]
    drafts=[RevisionService(repo).draft(d) for d in ids]
    items.append({'candidate_id':identity,'revision':candidate['revision'],'row_version':repo.get_candidate(identity)['row_version'],'triple':candidate['triple'],
                  'source_support':decision.get('source_support_result'),'source_votes':[{k:v.get(k) for k in ('voter_id','source_support','schema_issue','reason_codes','checks','suggested_revision')} for v in audit['votes'] if proposal and v['round_id']==proposal['round_id']],
                  'evidence':list(archive.values()),'drafts':[{'draft_id':d['draft_id'],'status':d['status'],'changes':d['changes'],'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','rationale_summary','field_reviews')} for v in d.get('review_result',{}).get('votes',[])]} for d in drafts]})
(root/'runs/candidate_revision_resolution_20261030/continuation_inspection.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
for item in items:
    print(json.dumps({k:item[k] for k in ('candidate_id','revision','row_version','triple','source_support')},ensure_ascii=False))
    for e in item['evidence']:print(e['evidence_id'],e.get('text',e.get('quote')))
    for d in item['drafts']:
        if d['status']!='applied':print(json.dumps(d,ensure_ascii=False))
