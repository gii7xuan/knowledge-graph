from pathlib import Path
import json
from review_agent.repository import ReviewRepository
ROOT=Path(__file__).resolve().parents[1]
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
with repo.connect() as c:
    queued=[dict(r) for r in c.execute("SELECT job_id FROM jobs WHERE status='queued' AND json_extract(payload_json,'$.planning_version')='ontology_extension_planner_v2'")]
    row=c.execute("SELECT p.proposal_json,p.audit_id FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE json_extract(a.context_json,'$.versions.planner_version')='ontology_extension_planner_v2' ORDER BY p.rowid DESC LIMIT 1").fetchone()
for job in queued: repo.request_cancel(job['job_id'])
if row:
    proposal=json.loads(row['proposal_json'])
    def sizes(value): return {k:len(json.dumps(v,ensure_ascii=False)) for k,v in value.items()}
    report={'audit_id':row['audit_id'],'characters':len(row['proposal_json']),'fields':sizes(proposal),
        'check_sizes':sizes(proposal['check_records']),'cancelled_queued_jobs':[r['job_id'] for r in queued]}
    if proposal.get('source_patch'): report['source_patch_sizes']=sizes(proposal['source_patch'])
    (ROOT/'runs/review_followup_20261030/payload_diagnosis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
else: print('no new frozen proposal yet')
