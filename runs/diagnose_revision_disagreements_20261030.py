"""Read-only inventory of current revision duties and actual reviewer conflicts."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/candidate_revision_resolution_20261030'
OUT.mkdir(exist_ok=True)
RUN='run_chapter1_20261028_resume1'


if __name__=='__main__':
    backup=OUT/('before_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
    target=sqlite3.connect(backup)
    try:source.backup(target)
    finally:target.close();source.close()
    repo=ReviewRepository(backup);revisions=RevisionService(repo)
    records=[]
    for candidate in repo.list_candidates(RUN,limit=1000000):
        if candidate['workflow_state'] not in {'revision_required','manual_review'}:continue
        audit=repo.get_audit(candidate['candidate_id']);decision=repo.latest_decision(candidate['candidate_id']) or {}
        selected=next((proposal for proposal in audit['rounds'] if proposal['round_id']==decision.get('selected_round_id')),
                      audit['rounds'][-1] if audit['rounds'] else None)
        votes=[vote for vote in audit['votes'] if selected and vote['round_id']==selected['round_id'] and vote.get('response_status')=='ok']
        contexts=audit['context'];state=(audit.get('agent_state') or {}).get('state',{})
        issues=contexts.get('unresolved_issues') or [issue for issue in contexts.get('issue_history',state.get('issues',[])) if issue.get('status','open')!='resolved']
        with repo.connect() as connection:
            ids=[row[0] for row in connection.execute('SELECT draft_id FROM revision_drafts WHERE candidate_id=? ORDER BY rowid',(candidate['candidate_id'],))]
        drafts=[revisions.draft(identity) for identity in ids]
        source_states={vote.get('source_support') for vote in votes}
        material={eid for vote in votes for eid in vote.get('counter_evidence_ids',[])}
        codes={code for vote in votes for code in vote.get('reason_codes',[])}|{issue['code'] for issue in issues}
        needs_revision=bool(candidate['workflow_state']=='revision_required' or codes&{'CONDITION_MISSING','QUALIFIER_MISMATCH','DIRECTION_MISMATCH','revision_proposed'})
        genuine_conflict=bool(material or len(source_states-{'supported'})>0 and 'supported' in source_states or codes&{'source_conflict','counterevidence'})
        category=('revision_and_source_conflict' if needs_revision and genuine_conflict else 'revision_required' if needs_revision
                  else 'source_disagreement' if genuine_conflict else 'incomplete_review' if not contexts.get('independent_review_completed')
                  else 'other_manual')
        record={'candidate_id':candidate['candidate_id'],'revision':candidate['revision'],'row_version':candidate['row_version'],
            'workflow_state':candidate['workflow_state'],'audit_id':audit['audit_id'],
            'independent_completed':bool(contexts.get('independent_review_completed')),
            'source_support_result':decision.get('source_support_result'),'category':category,
            'triple':candidate['triple'],'reason_codes':sorted(codes),'issues':issues,
            'selected_proposal_digest':selected['proposal_digest'] if selected else None,
            'evidence':selected['evidence_bundle']['items'] if selected else [],
            'protected_evidence_ids':sorted(material|set(state.get('protected_ids',[]))),
            'votes':[{key:vote.get(key) for key in ('voter_id','verdict','source_support','schema_issue','reason_codes',
                'suggested_revision','requested_context','supporting_evidence_ids','counter_evidence_ids','checks','evidence_assessments')} for vote in votes],
            'drafts':[{key:draft.get(key) for key in ('draft_id','base_revision','expected_row_version','status','changes','reason','evidence_ids','review_result')} for draft in drafts]}
        records.append(record)
    with repo.connect() as connection:
        draft_counts=[dict(row) for row in connection.execute('SELECT status,COUNT(*) n FROM revision_drafts GROUP BY status')]
    summary={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'backup':str(backup),'run_id':RUN,
        'candidates_examined':len(records),'workflow_states':dict(Counter(row['workflow_state'] for row in records)),
        'categories':dict(Counter(row['category'] for row in records)),
        'independent_completion':dict(Counter(str(row['independent_completed']) for row in records)),
        'candidates_with_typed_revision_hints':sum(any(isinstance(vote.get('suggested_revision'),dict) and vote['suggested_revision'] for vote in row['votes']) for row in records),
        'draft_statuses':draft_counts,'external_model_calls':0,'production_database_modified':False}
    (OUT/'cases.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'diagnosis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
