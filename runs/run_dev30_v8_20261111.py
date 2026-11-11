"""One bounded fresh smoke followed by the authorized full development rerun."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from runs.regress_v8 import ROOT, fingerprints
from runs.revalidate_v8 import digest, read

CONTROL = ROOT / 'outputs/dev30_v8_launch_20261111'
SMOKE = ROOT / 'outputs/dev7_v8_fresh_20261111'
FULL = ROOT / 'outputs/dev30_v8_full_20261111'
PREFLIGHT = ROOT / 'outputs/dev30_v8_prerun_20261111_r2/preflight.json'


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def save(name, value):
    write_status_snapshot(CONTROL / name, value)


def run(module, *args):
    command = [sys.executable, '-X', 'utf8', '-B', '-m', module, *map(str, args)]
    print(json.dumps({'at': now(), 'command': command}, ensure_ascii=False), flush=True)
    subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)


def main():
    if CONTROL.exists() or SMOKE.exists() or FULL.exists():
        raise ValueError('Never relaunch this experiment or overwrite its receipts')
    probe = read(PREFLIGHT)
    if probe['status'] != 'ready' or not 0 <= time.time() - probe['finished_at_epoch'] <= 600:
        raise ValueError('Fresh three-seat service availability check required')
    CONTROL.mkdir()
    bindings = read(ROOT / 'outputs/dev30_20261031/sample_data.json')['bindings']
    production = ReadOnlyRepository(ROOT / 'runs/v3/review.sqlite3')
    paths = [
        'outputs/dev30_20261031/sample_data.json',
        'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx',
        'outputs/dev30_v8_rerun_20261109/review.sqlite3',
        'outputs/dev30_v8_full_20261110_0913/review.sqlite3',
        'outputs/dev30_v8_full_20261110_0913/smoke_report.json',
        'outputs/dev30_v8_revalidated_20261110_final/review.sqlite3',
        'outputs/dev30_v8_revalidated_20261110_final/smoke_report.json',
        'outputs/dev30_v8_focus_20261110_final/annotation_first5/annotations.json',
        'outputs/dev5_v8_human_20261110_150238/human_submission.json',
    ]
    protected = {
        'sha256': {p: digest(ROOT / p) for p in paths},
        'production_candidate_hashes': {
            b['candidate_id']: sha256_json(production.get_revision(b['candidate_id'])) for b in bindings
        },
        'new_run_isolated': True, 'historical_votes_imported': False, 'human_labels_in_prompt': False,
    }
    for b in bindings:
        assert protected['production_candidate_hashes'][b['candidate_id']] == b['candidate_content_hash']
    frozen = fingerprints()
    save('preserved_inputs.json', protected)
    save('launcher.json', {'at': now(), 'python': sys.executable, 'python_version': sys.version,
                          'launcher_sha256': digest(Path(__file__)), 'service_probe': probe,
                          'smoke_output': str(SMOKE), 'full_output': str(FULL),
                          'no_automatic_relaunch': True, 'no_extra_DEV016_trials': True})
    try:
        for mode, directory in [('smoke', SMOKE), ('full', FULL)]:
            if fingerprints() != frozen:
                raise ValueError('Execution code changed; do not launch another stage')
            save('status.json', {'active': True, 'stage': mode, 'at': now(), 'output': str(directory)})
            args = ['--mode', mode, '--output', directory, '--run']
            if mode == 'full':
                args += ['--smoke-gate', SMOKE]
            run('runs.regress_v8', *args)
            shutil.copyfile(CONTROL / 'preserved_inputs.json', directory / 'preserved_inputs.json')
            run('runs.finalize_v8_regression', '--directory', directory)
            run('runs.report_v8_regression', '--directory', directory)
            if mode == 'smoke' and not read(directory / 'smoke_report.json')['engineering_gate_passed']:
                raise ValueError('Current-version engineering gate failed; full run not started')
        save('status.json', {'active': False, 'stage': 'completed', 'at': now(),
                             'full_summary': read(FULL / 'summary.json'), 'production_written': False})
    except Exception as exc:
        save('status.json', {'active': False, 'stage': 'stopped', 'at': now(),
                             'error_type': type(exc).__name__, 'error': str(exc),
                             'no_automatic_relaunch': True, 'production_written': False})
        raise


if __name__ == '__main__':
    main()
