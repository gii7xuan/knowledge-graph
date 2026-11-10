"""Recompute a completed regression's diagnostics only; never replay model calls."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
from contextlib import ExitStack
from unittest.mock import patch
from review_agent.progress import write_status_snapshot
from evaluation.review_samples import ReadOnlyRepository
from runs.regress_v8 import fingerprints,summarize,ROOT
from runs.revalidate_v8 import digest,read
from review_agent.contracts import sha256_json


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    source=args.source.resolve();out=args.output.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'outputs') or out.is_relative_to(source):raise ValueError('use a new separate output directory')
    original=read(source/'smoke_report.json');frozen=read(source/'code_fingerprints.json');current=fingerprints()
    changes=[name for name in sorted(set(frozen)|set(current)) if frozen.get(name)!=current.get(name)]
    bindings=read(source/'sample_bindings.json')
    trace_only_failure=(original.get('all_have_explainable_terminal_state') and original.get('known_trace_defect_recurred') and
        not original.get('isolated_import_outbox') and not original.get('isolated_revisions_applied') and
        not any(r.get('runtime_error') or r.get('systematic_version_or_binding_error') for r in original['cases']))
    if ((not original.get('engineering_gate_passed') and not trace_only_failure) or
        len(original['cases'])!=len(bindings) or set(r['sample_id'] for r in original['cases'])!=set(b['sample_id'] for b in bindings) or
        set(changes)-{'review_agent/review_observability.py','review_agent/response_contract_v6_2.py'}):
        raise ValueError('request execution changed or an unrelated engineering fault exists; run a new real regression')
    # SQLite's -shm contains volatile reader-lock/read-mark bytes, not persisted votes.
    # Protect the actual database, WAL and every experiment artifact byte-for-byte.
    def protection():return {str(p):digest(p) for p in source.rglob('*') if p.is_file() and not p.name.endswith('-shm')}
    protected=protection();out.mkdir(parents=True)
    for name in ('settings.json','ancestry.json','sample_bindings.json','progress.json','preserved_inputs.json'):
        if not (source/name).exists():continue
        shutil.copyfile(source/name,out/name)
    write_status_snapshot(out/'experiment.json',{'mode':'offline_diagnostic_revalidation','partition':'development',
        'source_experiment':str(source),'source_experiment_manifest':read(source/'experiment.json'),
        'settings_hash':sha256_json(read(source/'settings.json')),'current_code_hash':sha256_json(current),
        'changed_files':changes,'new_model_calls':0,'production_written':False})
    with sqlite3.connect((source/'review.sqlite3').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(out/'review.sqlite3') as dst:src.backup(dst)
    write_status_snapshot(out/'code_fingerprints.json',current)
    with ExitStack() as stack:
        for name in ('OpenAICompatibleClient','MockLLMClient'):
            stack.enter_context(patch('review_agent.llm_clients.'+name+'.complete_json',side_effect=RuntimeError('offline smoke revalidation prohibits model calls')))
        report=summarize(ReadOnlyRepository(out/'review.sqlite3'),read(out/'sample_bindings.json'),out)
    report.update(mode='fresh_source_regression_plus_offline_diagnostic_revalidation',source_experiment=str(source),
        recorded_committee_request_attempts=report['new_committee_request_attempts'],recorded_planner_calls=report['planner_calls'],
        new_committee_request_attempts=0,planner_calls=0,new_model_calls=0,changed_code_files=changes,
        diagnostic_changes=['rejected raw-only envelopes validate immutable merge without inventing a stored normalized view',
            'schema/semantic failures remain ineligible; valid or partially stored views still require exact comparison'],
        core_request_contracts_and_execution_unchanged=True,response_provenance_validator_updated='review_agent/response_contract_v6_2.py' in changes)
    write_status_snapshot(out/'smoke_report.json',report)
    if protected!=protection():raise ValueError('original smoke changed')
    write_status_snapshot(out/'derivation.json',{'mode':'offline_diagnostics_only','source_files_sha256':protected,
        'source_code_fingerprints':frozen,'current_code_fingerprints':current,'changed_files':changes,'new_model_calls':0,
        'historical_database_unchanged':True,'volatile_sqlite_shm_excluded':True,'production_written':False})
    print(json.dumps({'output':str(out),'gate':report['engineering_gate_passed'],'new_model_calls':0,'changed_code_files':changes},ensure_ascii=False))


if __name__=='__main__':main()
