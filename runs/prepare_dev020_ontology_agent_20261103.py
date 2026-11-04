"""Prepare the real development extension task without dispatching ontology votes."""
from copy import deepcopy
import json
from pathlib import Path
from review_agent.cli import _configured_validators
from review_agent.model_settings import create_planner_client
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from ontology_review.preparation import OntologyPreparationRuntime
from ontology_review.service import effective_config

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_mapping_source_20261103'


def main():
    repo = ReviewRepository(OUT / 'development_review.sqlite3')
    summary = json.loads((OUT / 'source_summary.json').read_text(encoding='utf-8'))
    row = next(r for r in summary['results'] if r.get('pending_extension_id'))
    task = repo.extension_status(row['pending_extension_id'])
    if task.get('delta'):
        print(json.dumps({'existing_delta_preserved': task['extension_id']}, ensure_ascii=False)); return
    config = deepcopy(load_config()); config['mode'] = 'shadow'
    options = config.setdefault('ontology_extension', {}); options['auto_publish_enabled'] = False
    options['budget'] = {**options.get('budget', {}), 'max_planner_steps': 8, 'max_tool_calls': 12,
                         'max_llm_calls_per_candidate': 8, 'max_total_tokens': 120000,
                         'total_timeout_seconds': 240, 'max_retries_per_request': 0}
    runtime = OntologyPreparationRuntime(repo, ROOT / 'runs/ontology_releases', _configured_validators(config),
                                         create_planner_client(effective_config(config)), config)
    result = runtime.run(task['extension_id'])
    write_status_snapshot(OUT / 'DEV-020_ontology_agent_preparation.json', result)
    current = repo.extension_status(task['extension_id'])
    write_status_snapshot(OUT / 'DEV-020_ontology_task_prepared.json', current)
    print(json.dumps({k: result.get(k) for k in ('extension_id', 'audit_id', 'recommendation', 'stop_reason',
                                               'operator_preparation_only', 'independent_review_completed', 'release_published')},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
