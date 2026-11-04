"""Refresh only task CAS/next-work metadata after the local draft corrections."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/ontology_draft_repairs_20261030'
receipt=json.loads((OUT/'application_receipt.json').read_text(encoding='utf-8'))
prior=json.loads((ROOT/'runs/review_followup_20261030/offline_preparation/resumption_plan.json').read_text(encoding='utf-8'))
source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
copy=sqlite3.connect(':memory:');source.backup(copy);source.close();copy.row_factory=sqlite3.Row
lanes={'reuse_existing_definition':'candidate_representation_review',
    'explicit_alternative_constraint_modeling_required':'constraint_remodeling',
    'correct_alias_owner_with_minimal_new_class':'independent_new_class_and_alias_review',
    'correct_exact_named_endpoint_id':'candidate_type_review_then_independent_ontology_review',
    'reviewer_clarification_required':'original_reviewer_clarification'}
items=[]
for original in prior['items']:
    row=copy.execute('SELECT row_version,status,task_json FROM ontology_extension_tasks WHERE extension_id=?',(original['extension_id'],)).fetchone()
    task=json.loads(row['task_json']);resolution=task.get('draft_repair_resolution') or {}
    items.append({'extension_id':original['extension_id'],'task_row_version':row['row_version'],
        'previous_plan_task_row_version':original['task_row_version'],'task_status':row['status'],
        'delta_revision':task['delta_revision'],'base_release_id':task['base_release_id'],
        'active_delta_hash':(task.get('delta') or {}).get('delta_hash'),
        'lane':lanes.get(resolution.get('disposition'),original['lane']),
        'repair_resolution':resolution or None,
        'prior_verified_dossier':original['dossier'],
        'unknown_request_keys_do_not_resend':original['unknown_request_keys_do_not_resend'],
        'old_votes_reused_as_new_approval':False,
        'execution_preconditions':['reload_current_CAS_and_source_bindings',
            'resolve_task_specific_modeling_or_candidate_obligations',
            'recheck_budget_without_resetting_usage',
            'reconcile_unknown_requests_before_equivalent_retry',
            'complete_original_independent_committee_and_semantic_competency_checks']})
copy.close()
result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'tasks':len(items),
    'lanes':dict(Counter(item['lane'] for item in items)),
    'application_receipt':str(OUT/'application_receipt.json'),
    'old_plan_CAS_values_superseded_for_changed_tasks':True,
    'model_calls':0,'automatic_dispatch':False,'automatic_publication':False,'items':items}
(OUT/'next_work_items.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in result.items() if key!='items'},ensure_ascii=False,indent=2))
