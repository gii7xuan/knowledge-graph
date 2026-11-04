"""Current extension/review/publication inventory, preserving a pre-action snapshot."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import json,sqlite3
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from ontology_review.publisher import OntologyPublisher,current_release_id

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031';OUT.mkdir(exist_ok=True)
backup=OUT/'before.sqlite3'
if not backup.exists():
    source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=30)
    target=sqlite3.connect(backup)
    try:source.backup(target)
    finally:target.close();source.close()
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');publisher=OntologyPublisher(repo,ROOT/'runs/ontology_releases',load_config())
head=current_release_id(ROOT/'runs/ontology_releases');items=[]
with repo.connect() as connection:task_ids=[r[0] for r in connection.execute('SELECT extension_id FROM ontology_extension_tasks ORDER BY rowid')]
for task in (repo.extension_status(identity) for identity in task_ids):
    identity=task['extension_id']
    with repo.connect() as connection:
        ids=[r[0] for r in connection.execute('SELECT audit_id FROM ontology_audits WHERE extension_id=? ORDER BY rowid',(identity,))]
    audits=[]
    for aid in ids:
        audit=repo.ontology_audit(aid)
        audits.append({'audit_id':aid,'status':audit['status'],'delta_revision':audit['delta_revision'],
            'result':audit.get('result'),'proposals':[{'round_id':p['round_id'],'proposal_digest':p['proposal_digest'],'object_revision':p['object_revision'],
                'delta_hash':p['delta_hash'],'evidence_count':len(p['evidence']),'checks':{k:v.get('status') for k,v in p['check_records'].items()}} for p in audit['proposals']],
            'votes':[{k:v.get(k) for k in ('voter_id','round_id','response_status','decision','outcome_unknown','error_code','error','reason_codes','request_key','rationale_summary','suggested_changes')} for v in audit['votes']]})
    triggers=[]
    for t in task['trigger_candidates']:
        c=repo.get_candidate(t['candidate_id']);d=repo.latest_decision(t['candidate_id']) or {}
        triggers.append({'candidate_id':t['candidate_id'],'trigger_revision':t['revision'],'current_revision':c['revision'],
            'row_version':c['row_version'],'source_support':d.get('source_support_result'),'workflow_state':c['workflow_state'],
            'subscription_matches_current':any(s['candidate_id']==c['candidate_id'] and s['revision']==c['revision'] and s['expected_row_version']==c['row_version'] and s['status']=='active' for s in task['subscriptions'])})
    row={'extension_id':identity,'status':task['status'],'row_version':task['row_version'],'delta_revision':task['delta_revision'],
        'base_release_id':task['base_release_id'],'base_is_head':task['base_release_id']==head,'change_kind':task['change_kind'],
        'description':task['description'],'delta':task.get('delta'),'triggers':triggers,'audits':audits}
    try:
        preview=publisher.preview(identity)
        row['publishable_review']=True;row['publication_preview']={k:preview[k] for k in ('expected_row_version','base_release_id','proposal_digest','delta_hash','profile_hash','mapping_report_hash','requires_difference_confirmation','risk_category','operations','impact')}
    except Exception as exc:row['publishable_review']=False;row['publication_blocker']=str(exc)
    items.append(row)
result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'backup':str(backup),'head':head,
    'task_count':len(items),'states':dict(Counter(i['status'] for i in items)),
    'current_deltas':sum(i['delta'] is not None for i in items),'publishable_reviews':sum(i['publishable_review'] for i in items),'items':items}
(OUT/'inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False))
for row in items:
    if row['delta'] or any(a['votes'] for a in row['audits']):
        print(json.dumps({k:row[k] for k in ('extension_id','status','delta_revision','change_kind','publishable_review','publication_blocker') if k in row}|{
            'operations':[{k:o.get(k) for k in ('kind','target','changes')} for o in (row['delta'] or {}).get('operations',[])],
            'current_subscribers':sum(t['subscription_matches_current'] for t in row['triggers']),
            'audits':[{'audit_id':a['audit_id'],'status':a['status'],'delta_revision':a['delta_revision'],
                'recommendation':(a['result'] or {}).get('recommendation'),'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','outcome_unknown')} for v in a['votes']]} for a in row['audits']]},ensure_ascii=False))
