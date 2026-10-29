"""Inspect only the restarted batch, using an immutable snapshot for analysis."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_quality_checks/chapter1_v3_20261028'


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def parsed(value): return json.loads(value) if value else {}
def save(name,data): (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    from document.hierarchy_index import load_document_index
    from review_agent.consensus import Committee,aggregate_votes
    pointer=read(ROOT/'runs/review_jobs/current.json')
    job=Path(pointer['job_dir']);progress=read(job/'status.json')
    execution_id=progress['execution_id'];run_id=progress['run_id']
    OUT.mkdir(parents=True,exist_ok=True)
    moment=datetime.now(timezone.utc)
    snapshot=OUT/('snapshot_'+moment.strftime('%Y%m%d_%H%M%S')+'.sqlite3')
    source=sqlite3.connect('file:'+str(ROOT/'runs/v3/review.sqlite3')+'?mode=ro',uri=True,timeout=15)
    try:
        target=sqlite3.connect(snapshot)
        try: source.backup(target)
        finally: target.close()
    finally: source.close()
    connection=sqlite3.connect('file:'+str(snapshot)+'?immutable=1',uri=True)
    connection.row_factory=sqlite3.Row
    manifest=parsed(connection.execute('SELECT manifest_json FROM runs WHERE run_id=?',(run_id,)).fetchone()[0])
    document=load_document_index(manifest['document_path'],manifest['source_index_hash'],require_verified=True)
    text=document['source_snapshot']['canonical_text']
    all_audits=[dict(row) for row in connection.execute('SELECT a.* FROM audit_runs a JOIN candidates c ON c.candidate_id=a.candidate_id WHERE c.run_id=?',(run_id,))]
    audits=[a for a in all_audits if parsed(a['context_json']).get('execution_id')==execution_id]
    completed=[a for a in audits if a['status']=='completed']
    counts={key:Counter() for key in ('completion','strategies','stop_reasons','decisions','workflows','vote_statuses',
        'model_votes','source_consensus','schema_status','planner_errors','invalid_tools','tools','tool_statuses')}
    violations=[];rows=[]
    for audit in completed:
        aid=audit['audit_id'];cid=audit['candidate_id'];ctx=parsed(audit['context_json'])
        candidate=parsed(connection.execute('SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?',(cid,audit['revision'])).fetchone()[0])
        state_row=connection.execute('SELECT checkpoint_json FROM agent_checkpoints WHERE audit_id=?',(aid,)).fetchone()
        state=parsed(state_row[0]).get('state',{}) if state_row else {}
        decision_row=connection.execute('SELECT decision_json FROM decisions WHERE audit_id=? ORDER BY decision_version DESC LIMIT 1',(aid,)).fetchone()
        decision=parsed(decision_row[0]) if decision_row else {}
        proposals={r['round_id']:parsed(r['proposal_json']) for r in connection.execute('SELECT * FROM proposals WHERE audit_id=?',(aid,))}
        votes=[parsed(r[0]) for r in connection.execute('SELECT vote_json FROM validator_votes WHERE audit_id=?',(aid,))]
        tools=[dict(r)|parsed(r['record_json']) for r in connection.execute('SELECT * FROM agent_tool_calls WHERE audit_id=? ORDER BY step_no',(aid,))]
        steps=[parsed(r[0]) for r in connection.execute('SELECT record_json FROM planner_steps WHERE audit_id=? ORDER BY step_no',(aid,))]
        final_round=decision.get('final_round_id')
        current=proposals.get(final_round)
        current_votes=[v for v in votes if v.get('round_id')==final_round]
        roster=ctx.get('committee_members',[])
        config=read(job/'review_config.json')['consensus']
        committee=Committee([m['id'] for m in roster],config['required_votes'],config['minimum_distinct_families'],version=config['version'])
        consensus=aggregate_votes(current_votes,committee,proposal=current,proposal_digest=current['proposal_digest']) if current else {}
        real_complete=bool(current and state.get('ontology_checked') and set(consensus.get('eligible_voter_ids',[]))=={m['id'] for m in roster}
            and consensus.get('distinct_family_count',0)>=config['minimum_distinct_families'])
        marked=bool(ctx.get('independent_review_completed'))
        if marked!=real_complete: violations.append({'candidate_id':cid,'issue':'independent_completion_label_mismatch'})
        for key in ('document_version','source_index_hash','ontology_version','ontology_release_id','profile_hash'):
            expected=document['document_version'] if key=='document_version' else manifest[key]
            if candidate.get(key)!=expected: violations.append({'candidate_id':cid,'issue':'candidate_binding','field':key})
        for proposal in proposals.values():
            for item in proposal['evidence_bundle'].get('items',[]):
                if item.get('anchor_valid') is True and item.get('offset_basis')=='canonical_document':
                    if text[item['start']:item['end']]!=item.get('quote'): violations.append({'candidate_id':cid,'issue':'evidence_slice_mismatch'})
        for vote in votes:
            proposal=proposals[vote['round_id']]
            fields=('audit_id','proposal_digest','ontology_version','ontology_release_id','profile_hash','document_version','source_index_hash','committee_version','committee_policy_hash','prompt_version')
            if any(vote.get(k)!=proposal.get(k) for k in fields): violations.append({'candidate_id':cid,'issue':'vote_binding_mismatch'})
            if vote.get('task_type')!='triple_review' or vote.get('task_id')!=cid or vote.get('object_revision')!=audit['revision']:
                violations.append({'candidate_id':cid,'issue':'vote_task_mismatch'})
            allowed={e['evidence_id'] for e in proposal['evidence_bundle']['items']}
            if not set(vote.get('supporting_evidence_ids',[])+vote.get('counter_evidence_ids',[]))<=allowed:
                violations.append({'candidate_id':cid,'issue':'vote_evidence_reference_mismatch'})
            if vote.get('evidence_hash')!=proposal['evidence_bundle']['evidence_hash']:
                violations.append({'candidate_id':cid,'issue':'vote_evidence_hash_mismatch'})
            counts['vote_statuses'][vote.get('response_status')]+=1
            counts['model_votes'][vote.get('model_id')]+=1
        seen_ontology=False;seen_validation=False
        for tool in tools:
            name=tool['tool_name'];status=tool['status']
            counts['tools'][name]+=1;counts['tool_statuses'][status]+=1
            if status=='invalid_action': counts['invalid_tools'][(name,tool.get('error'))]+=1
            if name=='inspect_ontology' and status=='ok': seen_ontology=True
            if name=='run_validation' and status in {'ok','cached'}:
                if not seen_ontology: violations.append({'candidate_id':cid,'issue':'validation_before_ontology'})
                seen_validation=True
            if name=='finish_review' and status=='finish_requested' and state.get('bundle',{}).get('items') and not seen_validation:
                violations.append({'candidate_id':cid,'issue':'finish_before_validation'})
            if name=='request_ontology_extension' and status=='waiting':
                if not seen_validation or decision.get('source_support_result')!='supported':
                    violations.append({'candidate_id':cid,'issue':'extension_without_source_consensus'})
                supporting={eid for v in current_votes if v.get('voter_id') in consensus.get('eligible_voter_ids',[])
                    for eid in v.get('supporting_evidence_ids',[])}
                if not set(tool.get('arguments',{}).get('evidence_ids',[]))<=supporting:
                    violations.append({'candidate_id':cid,'issue':'extension_uses_unreviewed_evidence'})
        for step in steps:
            for attempt in step.get('attempts',[]):
                if attempt.get('error'): counts['planner_errors'][attempt['error']]+=1
        classification=ctx.get('review_completion_status','missing')
        counts['completion'][classification]+=1
        counts['strategies'][ctx.get('executed_strategy')]+=1
        counts['stop_reasons'][ctx.get('agent_stop_reason')]+=1
        counts['decisions'][decision.get('decision')]+=1
        counts['workflows'][decision.get('workflow_state')]+=1
        counts['source_consensus'][decision.get('source_support_result')]+=1
        counts['schema_status'][decision.get('schema_status')]+=1
        if decision.get('decision')=='auto_accept' and not real_complete:
            violations.append({'candidate_id':cid,'issue':'automatic_accept_without_complete_review'})
        rows.append({'candidate_id':cid,'audit_id':aid,'triple':candidate.get('triple'),
            'classification':classification,'label_correct':marked==real_complete,
            'ontology_checked':state.get('ontology_checked'),'proposal_count':len(proposals),
            'vote_count':len(votes),'current_valid_voter_ids':consensus.get('eligible_voter_ids',[]),
            'vote_diagnostics':[{k:v.get(k) for k in ('voter_id','model_id','response_status','error','error_code','validation_errors','retryable','outcome_unknown','cached','source_support','schema_status','schema_issue')} for v in current_votes],
            'source_consensus':decision.get('source_support_result'),'workflow':decision.get('workflow_state'),
            'decision':decision.get('decision'),'stop_reason':ctx.get('agent_stop_reason'),
            'fallback_reason':ctx.get('fallback_reason'),'reason_codes':decision.get('reason_codes'),
            'tools':[{'tool':t['tool_name'],'status':t['status'],'error':t.get('error')} for t in tools],
            'planner_errors':[a.get('error') for s in steps for a in s.get('attempts',[]) if a.get('error')],
            'budget':ctx.get('agent_budget')})
    connection.close()
    counts['invalid_tools']={str(k):v for k,v in counts['invalid_tools'].items()}
    summary={'checked_at_beijing':moment.astimezone(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
        'job':str(job),'execution_id':execution_id,'snapshot':str(snapshot),'completed_audits_checked':len(rows),
        'audit_statuses':dict(Counter(a['status'] for a in audits)),
        'counts':{k:dict(v) for k,v in counts.items()},'violations':violations,'violation_count':len(violations),
        'without_any_vote':sum(r['vote_count']==0 for r in rows),'without_ontology_check':sum(not r['ontology_checked'] for r in rows),
        'independent_review_complete':sum(r['classification']=='independent_review_completed' for r in rows),
        'independent_review_incomplete':sum(r['classification']!='independent_review_completed' for r in rows),
        'progress_at_start':{k:progress.get(k) for k in ('completed','independent_review_completed','review_incomplete','failed','remaining')},
        'database_modified_by_inspection':False,'additional_model_calls':0}
    save('summary.json',summary);save('candidate_checks.json',rows)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
