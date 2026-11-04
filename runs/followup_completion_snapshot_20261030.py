"""Final read-only follow-up receipt with an online SQLite backup."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
from ontology_review.publisher import current_release_id

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
snapshot=OUT/('after_followup_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
target=sqlite3.connect(snapshot)
try: source.backup(target)
finally: target.close();source.close()
c=sqlite3.connect(snapshot.as_uri()+'?immutable=1',uri=True);c.row_factory=sqlite3.Row
revisions=json.loads((OUT/'revisions/status.json').read_text(encoding='utf-8'))
reaudits=json.loads((OUT/'required_reaudit/status.json').read_text(encoding='utf-8'))
obligations=json.loads((OUT/'obligation_reconciliation/receipt.json').read_text(encoding='utf-8'))
current=[]
for row in obligations['items']:
    candidate=dict(c.execute('SELECT candidate_id,current_revision,workflow_state,pending_extension_id FROM candidates WHERE candidate_id=?',(row['candidate_id'],)).fetchone())
    candidate['ontology_jobs']=[dict(r) for r in c.execute("SELECT job_id,status,progress_json FROM jobs WHERE job_type='ontology_extension' AND json_extract(payload_json,'$.extension_id')=? ORDER BY rowid DESC LIMIT 1",(candidate['pending_extension_id'],))]
    current.append(candidate)
audits=[dict(r) for r in c.execute("SELECT audit_id,status,result_json FROM ontology_audits WHERE json_extract(context_json,'$.versions.planner_version') IN ('ontology_extension_planner_v2','ontology_extension_planner_v3')")]
proposals=0;votes=[];full=0
for audit in audits:
    proposals+=c.execute('SELECT COUNT(*) FROM ontology_proposals WHERE audit_id=?',(audit['audit_id'],)).fetchone()[0]
    selected=json.loads(audit['result_json'] or '{}').get('selected_round_id')
    rows=[json.loads(r[0]) for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=? AND round_id=?',(audit['audit_id'],selected))]
    votes.extend(rows)
    if len({v['voter_id'] for v in rows if v.get('response_status')=='ok'})==3: full+=1
summary={'client_date':'2026-10-30','checked_at_utc':datetime.now(timezone.utc).isoformat(),'snapshot':str(snapshot),
    'revisions':{'selected':revisions['selected'],'processed':revisions['processed'],'applied':revisions['applied'],
        'outcomes':dict(Counter(r['status'] for r in revisions['results']))},
    'separate_prior_revision_review':'needs_human: one valid opposition retained',
    'new_revisions_independently_reviewed':reaudits['independent_completed'],
    'current_candidates':current,'new_version_ontology_requests':len(obligations['items']),
    'ontology_followup':{'audits_executed':len(audits),'frozen_proposals':proposals,'complete_three_seat_reviews':full,
        'selected_vote_statuses':dict(Counter(v['response_status'] for v in votes)),
        'selected_valid_decisions':dict(Counter(v.get('decision') for v in votes if v['response_status']=='ok'))},
    'jobs':[dict(r) for r in c.execute('SELECT job_type,status,COUNT(*) count FROM jobs GROUP BY job_type,status')],
    'budget_authorization_records':c.execute('SELECT COUNT(*) FROM case_budget_grants').fetchone()[0],
    'current_ontology_release':current_release_id(ROOT/'runs/ontology_releases'),
    'published_extension_tasks':c.execute("SELECT COUNT(*) FROM ontology_extension_tasks WHERE status='published'").fetchone()[0],
    'external_model_calls_for_snapshot':0}
(OUT/'completion_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2));c.close()
