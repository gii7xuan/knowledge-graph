"""Real V8 four-axis development regression of the fixed development population in an isolated database."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib
import json
import time
from pathlib import Path
import sqlite3
from uuid import uuid4

from document.hierarchy_index import load_document_index
from evaluation.manifest import freeze_manifest
from evaluation.review_samples import ReadOnlyRepository
from review_agent.batch import run_review_batch,versions_for,configured_members
from review_agent.contracts import sha256_json
from review_agent.model_settings import validator_settings,configured_planner
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/dev30_v8_rerun_20261109'
DATA=ROOT/'outputs/dev30_20261031/sample_data.json'
DB=OUT/'review.sqlite3'
BOOK=ROOT/'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx'
EXPECTED_BOOK='99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e'
TOOLS=['inspect_context','search_evidence','inspect_ontology','lookup_review_memory','run_validation','recover_validation','propose_revision','review_revision','review_dispute','finish_review']


def freeze_code():
    files=[p for folder in ('review_agent','evidence','schema','extract_triplets','confidence','document','ontologyBuild','evaluation')
           for p in (ROOT/folder).rglob('*.py')]
    files += list((ROOT/'prompts').glob('*.txt')) + list((ROOT/'specs').glob('*.json'))
    files += [ROOT/'config/review_agent.json',ROOT/'config/review_output_capabilities.json',
              Path(__file__),ROOT/'runs/report_dev30_v8_20261109.py',ROOT/'runs/watch_dev30_v8_20261109.py',
              ROOT/'runs/preflight_dev30_v8_20261109.py']
    files += [ROOT/'runs/repeat_dev016_v8_20261109.py']
    fingerprints={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    path=OUT/'code_fingerprints.json'
    if path.exists():
        assert json.loads(path.read_text(encoding='utf-8'))['files']==fingerprints,'experiment code changed: use a new experiment'
    else:
        save('code_fingerprints.json',{'files':fingerprints,'hash':sha256_json(fingerprints)})


def save(name,value):write_status_snapshot(OUT/name,value)


def configuration():
    c=load_config();c['validator_contract_version']='validator_response_v8'
    c=deepcopy(c)
    c['budget']={**c['budget'],'max_total_tokens':360000}
    c['case_budget']={**c.get('case_budget',{}),'tokens':400000}
    c.update(mode='shadow',database_path=str(DB),request_cache_namespace='dev30_v8_rerun_20261109')
    c['planner']={**c.get('planner',{}),'enabled':True,'strategy':'llm_react','allowed_tools':TOOLS}
    c['memory']={**c.get('memory',{}),'enabled':False,'scope':{'mode':'disabled'}}
    c['ontology_extension']={**c.get('ontology_extension',{}),'enabled':False,'auto_publish_enabled':False}
    c['routing']={**c.get('routing',{}),'auto_accept_enabled':False,'semantic_auto_reject_enabled':False}
    c['neo4j']={**c.get('neo4j',{}),'auto_import_enabled':False}
    return c


def public_settings(config):
    def safe(s):
        from urllib.parse import urlsplit
        u=urlsplit(s['base_url']);assert u.hostname in {'43.143.151.41','api.deepseek.com'}
        return {**{k:s.get(k) for k in ('model','family','model_identity','request_timeout_seconds','http_timeout_seconds',
                    'max_output_tokens','verify_ssl','thinking_mode','stream_responses')},
                'base_url':u.scheme+'://'+u.hostname+(':'+str(u.port) if u.port else '')+u.path}
    from review_agent.cli import _configured_validators
    from review_agent.output_structure import resolve_profile
    from review_agent.response_contract_v8 import RESPONSE_SCHEMA
    return {'output_profiles':[{'voter_id':v['id'],**resolve_profile(v,config,RESPONSE_SCHEMA)} for v in _configured_validators(config)],'mode':config['mode'],'versions':versions_for(config),'committee_members':configured_members(config),
        'consensus':config['consensus'],'budget':config['budget'],'case_budget':config.get('case_budget'),
        'planner':{**safe(configured_planner(config)),'allowed_tools':TOOLS},
        'validators':[{'id':v['id'],**safe(validator_settings(v))} for v in config['validators']],
        'scope_policy':config.get('scope_policy'),'human_labels_in_prompt':False,'review_memory_enabled':False,
        'sampling':{'temperature':0.0,'top_p':'not_sent_provider_default','seed':'not_sent','backend_weight_version':'not_pinned_by_model_alias'},
        'candidate_revisions_automatically_applied':False,'ontology_extension_execution_enabled':False,
        'ontology_publication_enabled':False,'graph_import_enabled':False}


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    assert OUT.name=='dev30_v8_rerun_20261109' and DB.parent==OUT
    freeze_code()
    data=json.loads(DATA.read_text(encoding='utf-8'));assert hashlib.sha256(BOOK.read_bytes()).hexdigest()==EXPECTED_BOOK
    live=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    for b,c in zip(data['bindings'],data['selected_raw_candidates']):
        assert b['candidate_content_hash']==sha256_json(c)==sha256_json(live.get_revision(c['candidate_id']))
    source=ReadOnlyRepository(data['source_database'])
    original=source.get_run(data['selected_raw_candidates'][0]['run_id'])
    first=data['selected_raw_candidates'][0]
    index=load_document_index(original['document_path'],first['source_index_hash'],require_verified=True)
    assert index['source_snapshot']['canonical_text']==data['source_canonical_text']
    c=configuration();settings=public_settings(c)
    if (OUT/'experiment.json').exists():
        saved=json.loads((OUT/'experiment.json').read_text(encoding='utf-8'))
        assert saved['settings_hash']==sha256_json(settings),'settings changed: freeze a new experiment, never overwrite tickets'
        assert saved['candidate_manifest_hash']==freeze_manifest(data['selected_raw_candidates'],run_id=first['run_id'])['manifest_hash']
        repo=ReviewRepository(DB)
        import_revision_provenance(source,repo,data)
        return data,repo,c
    repo=ReviewRepository(DB);repo.initialize()
    repo.create_run({'run_id':first['run_id'],'mode':'shadow','document_path':original['document_path'],
        'source_index_hash':first['source_index_hash'],'ontology_path':str(Path(first['ontology_release_path'])/'ontology_snapshot.json'),
        'ontology_hash':first['ontology_version'],'ontology_release_path':first['ontology_release_path'],
        'ontology_release_id':first['ontology_release_id'],'profile_hash':first['profile_hash'],
        'candidate_contract_version':'extraction_candidate_v4','experiment_id':'dev30_v8_rerun_20261109'})
    for candidate in data['selected_raw_candidates']:repo.add_candidate(deepcopy(candidate))
    import_revision_provenance(source,repo,data)
    manifest=freeze_manifest(data['selected_raw_candidates'],run_id=first['run_id'])
    save('candidate_manifest.json',manifest);save('sample_bindings.json',data['bindings']);save('settings.json',settings)
    save('experiment.json',{'experiment_id':'dev30_v8_rerun_20261109','evaluation_partition':'development',
        'prepared_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(), 'population':30,
        'candidate_manifest_hash':manifest['manifest_hash'],'source_workbook_sha256':EXPECTED_BOOK,
        'settings_hash':sha256_json(settings),'input_policy':'original_frozen_candidates_same_input_control',
        'labels_used_during_execution':False,'production_written':False})
    return data,repo,c


def import_revision_provenance(source,repo,data):
    """Copy immutable revision ancestry/draft evidence, never old votes or labels."""
    ids=[b['candidate_id'] for b in data['bindings']]
    draft_ids={c.get('revision_provenance',{}).get('draft_id') for c in data['selected_raw_candidates']}
    draft_ids.discard(None)
    imported_revisions=0;imported_drafts=0
    with source.connect() as src,repo.transaction(immediate=True) as dst:
        columns=[r[1] for r in src.execute('PRAGMA table_info(candidate_revisions)')]
        rows=src.execute('SELECT * FROM candidate_revisions WHERE candidate_id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
        for row in rows:
            imported_revisions+=dst.execute('INSERT OR IGNORE INTO candidate_revisions('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row)).rowcount
        for draft_id in sorted(draft_ids):
            row=src.execute('SELECT * FROM revision_drafts WHERE draft_id=?',(draft_id,)).fetchone()
            if row is None:raise ValueError('frozen source revision draft missing: '+draft_id)
            assert row['status']=='applied'
            columns=list(row.keys());values=[row[k] if k!='result_json' else '{}' for k in columns]
            imported_drafts+=dst.execute('INSERT OR IGNORE INTO revision_drafts('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values).rowcount
    for binding,candidate in zip(data['bindings'],data['selected_raw_candidates']):
        assert sha256_json(repo.get_revision(candidate['candidate_id']))==binding['candidate_content_hash']
    if imported_revisions or imported_drafts:
        save('revision_provenance_import.json',{'historical_revisions_added':imported_revisions,'applied_drafts_added':imported_drafts,
            'old_vote_results_imported':False,'human_labels_imported':False,'current_candidate_hashes_preserved':True})


def summarize(data,repo):
    rows=[];planner_attempts=[]
    for b in data['bindings']:
        cid=b['candidate_id'];decision=repo.latest_decision(cid) or {}
        try:a=repo.get_audit(cid,decision.get('audit_id'))
        except Exception:a={}
        rounds=a.get('rounds') or [];p=next((r for r in rounds if r['proposal_digest']==decision.get('selected_proposal_digest')),rounds[-1] if rounds else {})
        votes=[v for v in a.get('votes',[]) if v.get('proposal_digest')==p.get('proposal_digest')]
        planner_attempts.extend(attempt for step in a.get('planner_steps',[]) for attempt in step.get('attempts',[]))
        rows.append({'sample_id':b['sample_id'],'candidate_id':cid,'revision':b['revision'],
            'audit_id':a.get('audit_id'),'audit_status':a.get('status','not_started'),
            'independent_review_completed':bool(decision.get('independent_review_completed')),
            'source_support_result':decision.get('source_support_result'),'workflow_state':decision.get('workflow_state'),
            'expression_status':decision.get('consensus',{}).get('expression_status'),
            'mapping_status':decision.get('consensus',{}).get('mapping_status'),
            'effective_mapping_status':decision.get('consensus',{}).get('effective_mapping_status'),
            'core_source_support_result':decision.get('consensus',{}).get('core_source_support_result'),
            'knowledge_scope':decision.get('consensus',{}).get('knowledge_scope'),
            'ontology_context':({'context_hash':p.get('ontology_context_hash'),
                'characters':len(json.dumps(p['ontology_context'],ensure_ascii=False)),
                'properties':len(p['ontology_context']['properties']),'classes':len(p['ontology_context']['classes']),
                'full_catalog_included':p['ontology_context']['full_catalog_included']}
                if p.get('ontology_context') else None),
            'stop_reason':decision.get('stop_reason') or a.get('context',{}).get('agent_stop_reason')
                or a.get('context',{}).get('execution_failure',{}).get('reason_code'),
            'votes':[{k:v.get(k) for k in ('voter_id','model_id','response_status','source_support','verdict','expression_status','mapping','knowledge_scope','ontology_disagreement',
                'error_code','error','outcome_unknown','request_key','attempt_id','input_tokens','output_tokens','correction_terminal','correction_stop_reason','correction_available','response_origin','correction_trace')} for v in votes]})
    with repo.connect() as conn:
        attempts=conn.execute('SELECT state,result_json FROM request_attempts').fetchall()
        correction_rows=conn.execute('SELECT state,reserved_tokens,actual_tokens FROM review_response_corrections').fetchall()
    usage={'input_tokens':0,'output_tokens':0,'unknown_usage_attempts':0}
    for r in attempts:
        result=json.loads(r['result_json'] or '{}')
        known=False
        for name in ('input_tokens','output_tokens'):
            n=result.get(name)
            if type(n) is int:usage[name]+=n;known=True
        if not known:usage['unknown_usage_attempts']+=1
    summary={'population':30,'audits_started':sum(bool(r['audit_id']) for r in rows),
        'processing_closed':sum(r['audit_status'] in {'completed','interrupted','failed','timeout','paused'} for r in rows),
        'audits_finished':sum(r['audit_status']=='completed' for r in rows),
        'independent_review_completed':sum(r['independent_review_completed'] for r in rows),
        'source_support_counts':dict(Counter(r['source_support_result'] or 'not_finished' for r in rows)),
        'workflow_counts':dict(Counter(r['workflow_state'] or 'not_finished' for r in rows)),
        'field_corrections':{'reserved_or_attempted':len(correction_rows),'states':dict(Counter(r['state'] for r in correction_rows)),'reserved_tokens':sum(r['reserved_tokens'] for r in correction_rows),'known_actual_tokens':sum(r['actual_tokens'] for r in correction_rows)},'request_attempts':len(attempts),'attempt_state_counts':dict(Counter(r['state'] for r in attempts)),
        'usage':usage,'usage_scope':'durable committee requests; planner usage reported separately',
        'planner_calls':len(planner_attempts),'planner_usage':{k:sum(a.get(k) or 0 for a in planner_attempts) for k in ('input_tokens','output_tokens')},
        'cost_currency_amount':None,'cost_note':'provider price table not configured; report tokens and unknown usage separately',
        'production_written':False,'cases':rows}
    save('summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true')
    p.add_argument('--parallel',type=int,default=2)
    args=p.parse_args()
    data,repo,c=prepare()
    if args.parallel!=c['budget']['max_parallel_candidates']:
        raise ValueError('parallelism differs from frozen experiment settings')
    if args.run:
        probe=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
        assert probe.get('status')=='ready' and probe.get('settings_hash')==sha256_json(public_settings(c)), 'same-settings service preflight required'
        assert 0<=time.time()-probe['finished_at_epoch']<=600,'service preflight expired; check services again explicitly'
        ids=[b['candidate_id'] for b in data['bindings']]
        # Restarting this launcher selects only untouched cases; recovery is separate.
        with repo.connect() as conn: started={r[0] for r in conn.execute('SELECT DISTINCT candidate_id FROM audit_runs')}
        ids=[cid for cid in ids if cid not in started]
        if not ids:
            print(json.dumps({'status':'no_fresh_candidates','original_requests_preserved':True}));return
        stamp=uuid4().hex[:12]
        save('batch_settings_'+stamp+'.json',{'started_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'settings':public_settings(c),'candidate_ids':ids,
            'parallel_candidates':args.parallel,'fresh_only':True,
            'budget_note':'same audit/case token ceilings as prior fixed-sample experiment; V8 correction and clarification share frozen ceilings'})
        if (OUT/'last_batch.json').exists():
            previous=json.loads((OUT/'last_batch.json').read_text(encoding='utf-8'))
            save('batch_'+previous['execution_id']+'.json',previous)
        result=run_review_batch(repo,data['selected_raw_candidates'][0]['run_id'],c,
            candidate_ids=ids,
            status_file=OUT/'progress.json')
        save('last_batch.json',result);save('batch_'+result['execution_id']+'.json',result)
    result=summarize(data,repo)
    print(json.dumps({k:result[k] for k in ('population','audits_started','audits_finished','independent_review_completed',
        'source_support_counts','request_attempts','attempt_state_counts','usage')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
