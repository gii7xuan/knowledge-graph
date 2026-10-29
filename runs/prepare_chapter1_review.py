"""Freeze a review job and check configured model availability without source text."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit,urlunsplit

ROOT=Path(__file__).resolve().parents[1]
JOB=ROOT/'runs/review_jobs/chapter1_20261028_review'
RUN_ID='run_chapter1_20261028_resume1'


def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',action='store_true')
    args=parser.parse_args()
    from review_agent.service import load_config
    from review_agent.model_settings import configured_planner,validator_settings
    from document.hierarchy_index import load_document_index
    from ontologyBuild.release_service import load_release
    config=load_config()
    database=ROOT/config['database_path']
    if not JOB.exists():
        JOB.mkdir(parents=True)
        with sqlite3.connect(database) as source,sqlite3.connect(JOB/'before_review.sqlite3') as backup:
            source.backup(backup)
        write(JOB/'review_config.json',config)
    else:
        config=load_config(JOB/'review_config.json')
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as connection:
        manifest=json.loads(connection.execute('SELECT manifest_json FROM runs WHERE run_id=?',(RUN_ID,)).fetchone()[0])
        count=connection.execute('SELECT count(*) FROM candidates WHERE run_id=?',(RUN_ID,)).fetchone()[0]
        audits=connection.execute('SELECT count(*) FROM audit_runs a JOIN candidates c ON a.candidate_id=c.candidate_id WHERE c.run_id=?',(RUN_ID,)).fetchone()[0]
        workflows=dict(connection.execute('SELECT workflow_state,count(*) FROM candidates WHERE run_id=? GROUP BY workflow_state',(RUN_ID,)))
    document=load_document_index(manifest['document_path'],manifest['source_index_hash'],require_verified=True)
    release=load_release(manifest['ontology_release_path'])
    assert release.manifest['ontology_version']==manifest['ontology_version']
    assert count==320
    slots=[configured_planner(config)]+[validator_settings(s) for s in config['validators']]
    def safe(s):
        u=urlsplit(s['base_url'])
        return {k:s.get(k) for k in ('id','model','family','verify_ssl')}|{'endpoint':urlunsplit((u.scheme,u.hostname+(':'+str(u.port) if u.port else ''),u.path,'',''))}
    summary={'run_id':RUN_ID,'candidate_count':count,'existing_audits':audits,'workflows':workflows,
        'ontology_release_id':release.manifest['release_id'],'source_index_hash':document['index_content_hash'],
        'models':[safe(s) for s in slots],'database_backup':str(JOB/'before_review.sqlite3'),
        'status':'prepared','prepared_at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    write(JOB/'preflight.json',summary)
    if not args.probe:
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return
    unique={}
    for s in slots: unique.setdefault((s['base_url'],s['api_key']),[]).append(s)
    def probe(group):
        from openai import OpenAI
        import httpx
        first=group[0];started=time.monotonic()
        client=OpenAI(api_key=first['api_key'],base_url=first['base_url'],timeout=25,max_retries=0,
            http_client=httpx.Client(trust_env=False,verify=first.get('verify_ssl') is not False))
        record={'endpoint':safe(first)['endpoint'],'requested_models':list(dict.fromkeys(s['model'] for s in group))}
        try:
            models=[m.id for m in client.models.list().data]
            record.update(status='reachable',configured_model_available={s['model']:s['model'] in models for s in group},available_models=models)
        except Exception as exc:
            record.update(status='failed',error_type=type(exc).__name__,http_status=getattr(exc,'status_code',None))
        finally: client.close()
        record['latency_ms']=int((time.monotonic()-started)*1000)
        return record
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(probe,unique.values()))
    write(JOB/'model_probe.json',{'data_sent':'model listing only; no document or candidate content','results':results})
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
