"""Inspect completed audits using a consistent backup of the live review DB."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
RUN_ID='run_chapter1_20261028_resume1'
OUT=ROOT/'runs/review_quality_checks/chapter1_20261028'


def parse(value,default=None): return json.loads(value) if value else ({} if default is None else default)
def write(name,data): (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    from document.hierarchy_index import load_document_index
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    OUT.mkdir(parents=True,exist_ok=True)
    snapshot=OUT/('review_snapshot_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'.sqlite3')
    with sqlite3.connect('file:'+str(ROOT/'runs/v3/review.sqlite3')+'?mode=ro',uri=True,timeout=15) as source:
        with sqlite3.connect(snapshot) as target: source.backup(target)
    connection=sqlite3.connect('file:'+str(snapshot)+'?immutable=1',uri=True)
    connection.row_factory=sqlite3.Row
    manifest=parse(connection.execute('SELECT manifest_json FROM runs WHERE run_id=?',(RUN_ID,)).fetchone()[0])
    document=load_document_index(manifest['document_path'],manifest['source_index_hash'],require_verified=True)
    text=document['source_snapshot']['canonical_text']
    guard=OntologyGuard(load_snapshot(manifest['ontology_path'],manifest['ontology_version']))
    candidates={r['candidate_id']:dict(r) for r in connection.execute('SELECT * FROM candidates WHERE run_id=?',(RUN_ID,))}
    revisions={(r['candidate_id'],r['revision']):parse(r['candidate_json']) for r in connection.execute(
        'SELECT r.* FROM candidate_revisions r JOIN candidates c ON c.candidate_id=r.candidate_id WHERE c.run_id=?',(RUN_ID,))}
    audits=[dict(r) for r in connection.execute('SELECT a.*,a.rowid AS sequence FROM audit_runs a JOIN candidates c ON c.candidate_id=a.candidate_id WHERE c.run_id=? ORDER BY a.created_at,a.rowid',(RUN_ID,))]
    latest={}
    for audit in audits:
        if audit['status']=='completed' and audit['revision']==candidates[audit['candidate_id']]['current_revision']:
            latest[audit['candidate_id']]=audit
    counts={key:Counter() for key in ('strategies','stop_reasons','fallback_reasons','decisions','schema_check',
        'planner_statuses','planner_errors','tool_statuses','tools','vote_statuses','voter_models','reason_codes','gaps')}
    rows=[];sample_rows=[]
    for cid,audit in latest.items():
        aid=audit['audit_id'];candidate=revisions[cid,audit['revision']]
        context=parse(audit['context_json'])
        proposals=[parse(r[0]) for r in connection.execute('SELECT proposal_json FROM proposals WHERE audit_id=? ORDER BY round_id',(aid,))]
        votes=[parse(r[0]) for r in connection.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? ORDER BY round_id,voter_id',(aid,))]
        steps=[parse(r[0]) for r in connection.execute('SELECT record_json FROM planner_steps WHERE audit_id=? ORDER BY step_no',(aid,))]
        tools=[dict(r) for r in connection.execute('SELECT tool_name,status,record_json FROM agent_tool_calls WHERE audit_id=? ORDER BY rowid',(aid,))]
        decision_row=connection.execute('SELECT * FROM decisions WHERE audit_id=? ORDER BY decision_version DESC LIMIT 1',(aid,)).fetchone()
        decision=dict(decision_row) if decision_row else {}
        detail=parse(decision.get('decision_json'))
        state_row=connection.execute('SELECT checkpoint_json FROM agent_checkpoints WHERE audit_id=?',(aid,)).fetchone()
        state=parse(state_row[0]).get('state',{}) if state_row else {}
        check=guard.check(candidate)
        anchors=candidate.get('evidence_items') or []
        exact=[a for a in anchors if isinstance(a.get('start'),int) and isinstance(a.get('end'),int)
            and text[a['start']:a['end']]==a.get('quote')]
        checks_ok=(candidate.get('ontology_version')==manifest['ontology_version']
            and candidate.get('source_index_hash')==manifest['source_index_hash']
            and candidate.get('ontology_release_id')==manifest['ontology_release_id'])
        counts['strategies'][context.get('executed_strategy','missing')]+=1
        counts['stop_reasons'][context.get('agent_stop_reason') or state.get('stop_reason') or 'missing']+=1
        if context.get('fallback_reason'): counts['fallback_reasons'][context['fallback_reason']]+=1
        counts['decisions'][decision.get('final_decision','missing')]+=1
        counts['schema_check'][check['status']]+=1
        for step in steps:
            counts['planner_statuses'][step.get('status','missing')]+=1
            for attempt in step.get('attempts',[]):
                if attempt.get('error'): counts['planner_errors'][attempt['error']]+=1
        for tool in tools:
            counts['tools'][tool['tool_name']]+=1
            counts['tool_statuses'][tool['status']]+=1
        for vote in votes:
            counts['vote_statuses'][vote.get('response_status','missing')]+=1
            counts['voter_models'][vote.get('model_id','missing')]+=1
        counts['reason_codes'].update(parse(decision.get('reason_codes_json'),[]))
        gaps=detail.get('gaps') or state.get('gaps') or {}
        for key,value in gaps.items():
            if value is True: counts['gaps'][key]+=1
        valid_votes=[v for v in votes if v.get('response_status')=='ok']
        quorum=any(len({v.get('voter_id') for v in valid_votes if v.get('round_id')==number})>=3
            and len({v.get('model_family') for v in valid_votes if v.get('round_id')==number})>=3
            for number in {v.get('round_id') for v in valid_votes})
        row={'candidate_id':cid,'revision':audit['revision'],'audit_id':aid,'triple':candidate.get('triple'),
            'final_decision':decision.get('final_decision'),'reason_codes':parse(decision.get('reason_codes_json'),[]),
            'strategy':context.get('executed_strategy'),'stop_reason':context.get('agent_stop_reason') or state.get('stop_reason'),
            'fallback_reason':context.get('fallback_reason'),'schema_status':check['status'],'schema_reason_codes':check.get('reason_codes'),
            'source_anchor_count':len(anchors),'exact_source_anchor_count':len(exact),'bindings_match':checks_ok,
            'proposal_count':len(proposals),'vote_count':len(votes),'valid_vote_count':len(valid_votes),'three_family_quorum':quorum,
            'ontology_checked':bool(state.get('ontology_checked')),'workflow_state':candidates[cid]['workflow_state'],
            'planner_steps':steps,'tool_calls':tools,'gaps':gaps}
        rows.append(row)
        if len(sample_rows)<16:
            sample_rows.append({**row,'candidate':candidate,'audit_context':context,'agent_state':state,'proposals':proposals,'votes':votes})
    report={'run_id':RUN_ID,'snapshot':str(snapshot),'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'candidate_total':len(candidates),'completed_current_revision_candidates':len(rows),
        'all_audit_statuses':dict(Counter(a['status'] for a in audits)),
        'counts':{k:dict(v) for k,v in counts.items()},
        'without_proposal':sum(r['proposal_count']==0 for r in rows),
        'without_votes':sum(r['vote_count']==0 for r in rows),'three_family_quorum_count':sum(r['three_family_quorum'] for r in rows),
        'without_ontology_check':sum(not r['ontology_checked'] for r in rows),
        'with_exact_original_anchor':sum(r['exact_source_anchor_count']>0 for r in rows),
        'binding_mismatches':sum(not r['bindings_match'] for r in rows),
        'schema_pass_with_exact_source_but_no_votes':sum(r['schema_status']=='pass' and r['exact_source_anchor_count']>0 and r['vote_count']==0 for r in rows),
        'unjustified_auto_accepts':sum(r['final_decision']=='auto_accept' and not r['three_family_quorum'] for r in rows),
        'primary_database_changed_by_inspection':False}
    connection.close()
    write('completed_audit_checks.json',rows)
    write('sample_details.json',sample_rows)
    write('quality_summary.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
