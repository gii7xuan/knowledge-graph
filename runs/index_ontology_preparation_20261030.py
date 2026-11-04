"""Turn offline dossiers into an explicit, non-executing resumption plan."""
from collections import Counter
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs/review_followup_20261030/offline_preparation'
items = []
for path in sorted(OUT.glob('ext_*.json')):
    dossier = json.loads(path.read_text(encoding='utf-8'))
    checks = dossier.get('offline_delta_checks', {})
    stages = {name: checks.get(name, {}).get('status') for name in ('structure', 'impact', 'competency')}
    votes = [vote for audit in dossier.get('prior_reviews', []) for vote in audit['votes']]
    unknown = sorted({vote['request_key'] for vote in votes if vote.get('outcome_unknown') and vote.get('request_key')})
    valid = [vote for vote in votes if vote.get('response_status') == 'ok']
    budget_shortfalls = [{
        'case_id': account['case_id'],
        'exhausted_dimensions': [key for key, limit in account['limits'].items()
                                 if key in {'llm_calls', 'tokens', 'tool_calls'}
                                 and account['usage'].get(key, 0) >= limit]}
        for account in dossier.get('case_accounts', [])]
    budget_shortfalls = [account for account in budget_shortfalls if account['exhausted_dimensions']]
    steps = []
    if dossier['blockers']:
        lane = 'source_or_binding_blocked'
        steps.append('resolve_source_or_current_revision_binding_before_model_dispatch')
    elif any(value == 'fail' for value in stages.values()):
        lane = 'existing_delta_requires_revision'
        steps.append('repair_the_existing_delta_and_repeat_failed_checks')
    elif dossier.get('existing_delta'):
        lane = 'existing_delta_requires_context'
        steps.append('resolve_unknown_competency_checks_with_verifiable_context')
    else:
        lane = 'source_verified_requires_schema_investigation'
        steps.append('perform_full_RDF_lookup_and_draft_a_minimal_source_supported_delta')
    if any(vote.get('decision') in {'reject', 'revise'} for vote in valid):
        steps.append('address_existing_independent_objections_without_overwriting_votes')
    if unknown:
        steps.append('reconcile_original_unknown_requests_before_any_equivalent_retry')
    if budget_shortfalls:
        steps.append('check_remaining_budget_and_authorize_an_additive_grant_if_needed_preserving_usage')
    steps.extend(['recheck_current_CAS_and_source_hashes_at_execution_time',
                  'complete_structure_impact_and_competency_checks',
                  'obtain_all_original_three_model_independent_reviews_on_the_frozen_delta'])
    items.append({'extension_id': dossier['extension_id'], 'task_row_version': dossier['task_row_version'],
        'delta_revision': dossier['delta_revision'], 'base_release_id': dossier['base_release_id'],
        'evidence_hash': dossier.get('evidence_hash'), 'lane': lane, 'offline_checks': stages,
        'existing_valid_votes': valid, 'unknown_request_keys_do_not_resend': unknown,
        'budget_accounts_needing_attention': budget_shortfalls, 'next_steps': steps,
        'dossier': str(path)})

summary = {'external_model_calls': 0, 'production_database_modified': False,
    'automatic_dispatch': False, 'automatic_publication': False,
    'tasks': len(items), 'lanes': dict(Counter(item['lane'] for item in items)),
    'existing_delta_checks': {name: dict(Counter(item['offline_checks'][name] for item in items
        if item['offline_checks'][name] is not None)) for name in ('structure', 'impact', 'competency')},
    'unknown_request_count': len({key for item in items for key in item['unknown_request_keys_do_not_resend']}),
    'tasks_with_valid_objections': sum(any(vote.get('decision') in {'revise', 'reject'} for vote in item['existing_valid_votes']) for item in items),
    'tasks_with_budget_attention': sum(bool(item['budget_accounts_needing_attention']) for item in items),
    'model_dispatch_preconditions': ['same_model_service_available_for_long_context',
        'original_request_outcomes_reconciled_when_unknown', 'current_source_and_delta_bindings',
        'sufficient_task_and_case_budget_without_usage_reset'],
    'items': items}
(OUT / 'resumption_plan.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in summary.items() if key != 'items'}, ensure_ascii=False, indent=2))
