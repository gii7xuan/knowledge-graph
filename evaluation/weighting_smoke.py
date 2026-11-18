"""One bounded live Agent review in a new isolated DB; never resumes a launch.

Use --run to send up to eight total planner/reviewer requests. Without --run,
only prepare inspectable artifacts. Existing source records remain read-only.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from document.hierarchy_index import load_document_index
from .review_samples import ReadOnlyRepository
from .regression_gate import request_accounting
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.review_observability import redact

ROOT=Path(__file__).resolve().parents[1]


def save(directory,name,value):
    with (directory/name).open('x',encoding='utf-8') as stream:
        json.dump(redact(value),stream,ensure_ascii=False,indent=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--sample-id',default='DEV-006')
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    directory=Path(args.output).resolve()
    if not directory.is_relative_to(ROOT/'outputs') or directory.exists():
        raise ValueError('smoke requires a new separate directory under outputs')
    from runs.regress_v8 import DATA,configuration,public_settings
    data=json.loads(DATA.read_text(encoding='utf-8'))
    pairs=[(b,c) for b,c in zip(data['bindings'],data['selected_raw_candidates']) if b['sample_id']==args.sample_id]
    if len(pairs)!=1:raise ValueError('sample ID must select exactly one existing debugging candidate')
    binding,candidate=deepcopy(pairs[0])
    source=ReadOnlyRepository(data['source_database'])
    original=source.get_run(candidate['run_id'])
    before=sha256_json(source.get_revision(candidate['candidate_id'],candidate['revision']))
    if before!=binding['candidate_content_hash'] or before!=sha256_json(candidate):raise ValueError('debugging sample binding changed')
    document=load_document_index(original['document_path'],candidate['source_index_hash'],require_verified=True)
    directory.mkdir(parents=True)
    config=configuration(profile='weighted_diagnostic',directory=directory)
    repo=ReviewRepository(directory/'review.sqlite3');repo.initialize()
    repo.create_run({**original,'mode':'shadow','experiment_id':directory.name})
    repo.add_candidate(candidate)
    settings=public_settings(config)
    settings['vote_weighting']=config['vote_weighting']
    files=[p for folder in ('review_agent','confidence','evidence','document','ontologyBuild','schema','prompts','specs')
           for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    files.append(Path(__file__))
    import hashlib
    fingerprints={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    experiment={'kind':'isolated_live_weighting_mechanism_smoke','sample_id':args.sample_id,
        'candidate_hash':before,'source_database':str(source.database_path),'settings_hash':sha256_json(settings),
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'working_code_hash':sha256_json(fingerprints),'max_model_calls':8,'human_labels_used':False,
        'accuracy':None,'actual_semantic_revision_enabled':False,'production_writes_enabled':False}
    save(directory,'settings.json',settings);save(directory,'experiment.json',experiment)
    save(directory,'code_fingerprints.json',fingerprints);save(directory,'candidate.json',candidate)
    if not args.run:
        print(json.dumps({'status':'prepared','output':str(directory),'model_calls':0}));return
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import create_planner_client
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    save(directory,'started.json',{'automatic_relaunch':False,'max_model_calls':8})
    owner=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(Path(candidate['ontology_release_path'])/'ontology_snapshot.json')),
        _configured_validators(config),config=config,planner_client=create_planner_client(config))
    try:
        result=owner.audit_candidate(candidate['candidate_id'],candidate['revision'],document)
        save(directory,'result.json',result)
        audit=repo.get_audit(candidate['candidate_id'],result['audit_id'])
        save(directory,'audit.json',audit)
        with repo.connect() as connection:
            accounting=request_accounting(connection)
            states=[dict(r) for r in connection.execute("SELECT state,COUNT(*) AS count FROM request_attempts GROUP BY state")]
            calls=connection.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
            outbox=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
        after=sha256_json(source.get_revision(candidate['candidate_id'],candidate['revision']))
        report={'status':'completed','research_operation':result.get('research_operation'),
            'dimension_statuses':{a:d['status'] for a,d in result.get('review_outcome',{}).get('dimension_results',{}).items()},
            'model_request_attempts':calls,'request_states':states,'budget':result.get('budget'),
            'accounting':accounting,'weight_snapshot_id':result.get('weight_snapshot',{}).get('snapshot_id'),
            'source_candidate_unchanged':before==after,'isolated_import_outbox':outbox,'production_written':False,
            'accuracy':None,'human_labels_used':False,'failure':None}
    except Exception as exc:
        report={'status':'failed','failure':redact(str(exc)),'production_written':False,'accuracy':None}
        save(directory,'report.json',report)
        print(json.dumps(report,ensure_ascii=False));raise
    save(directory,'report.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
