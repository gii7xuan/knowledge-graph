"""Apply only the documented local draft corrections; never dispatch models."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import shutil
import sqlite3

from migrate_review_recovery_20261029 import fingerprints
from ontologyBuild.release_service import load_release
from ontology_review.delta import catalog
from ontology_review.draft_repair import repair_draft
from ontology_review.evidence import load_sources
from ontology_review.tools import OntologyTools
from review_agent.repository import ReviewRepository

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'runs/ontology_draft_repairs_20261030'
OUT.mkdir(exist_ok=True)
RELEASES = ROOT/'runs/ontology_releases'
PRIOR = ROOT/'runs/review_followup_20261030/offline_preparation'


def save_backup(database, prefix):
    target = OUT/(prefix+'_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    source = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=15)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    return target


def plan(repository):
    plans = []
    withdrawal = {
        'ext_0813e33a152048bc87c8fb055e1b0507': (
            'reuse_existing_definition',
            '采纳有效反对票：撤回将描述性句子构造成对象属性的操作；原文描述已由既有类定义承载。保留原来源支持，不将本体建模否定解释为原文否定。',
            {'reuse_schema_ids':['C_1693c5316c4746c54db6','C_4b4d774d036e0987e61b'],
             'candidate_representation_review_required':True, 'objection_disposition':'invalid_operation_withdrawn'}),
        'ext_47b2102b6ba646a1a4f40587e83c0561': (
            'reuse_existing_definition',
            '撤回重复承载既有类定义的数据属性；当前候选是定义性文字，不能为使实例关系映射通过而新增专用“定义”谓词。另行修复通用 xsd 前缀处理。',
            {'reuse_schema_ids':['C_b01fa0de9c5da123ef25'], 'candidate_representation_review_required':True}),
        'ext_3e7549fc23054ec5b03b783a72a169e2': (
            'explicit_alternative_constraint_modeling_required',
            '撤回将新 domain 类追加到 OWL 多重 domain 数组的草案：当前语义是交集，不是允许任一类；不能通过删旧约束或伪造父类使其通过。',
            {'reviewed_constraint_field':'domain','requires_definition_and_scope_review':True,'union_execution_not_supported_by_current_profile':True}),
        'ext_576152e0a5cb4b40ad1bf976dce05e9d': (
            'explicit_alternative_constraint_modeling_required',
            '撤回将新 range 类追加到 OWL 多重 range 数组的草案：当前语义是交集，不会扩大允许范围；需明确重建约束或关系后再做影响分析。',
            {'reviewed_constraint_field':'range','requires_definition_and_scope_review':True,'union_execution_not_supported_by_current_profile':True}),
        'ext_ffed3aeb1f66462fac7eb1d5d80f30e1': (
            'explicit_alternative_constraint_modeling_required',
            '撤回以多重 range 表示备选类型的草案，同时保留候选端点缺类型的问题；当前 OWL profile 不支持把交集暗中解释为并集。',
            {'reviewed_constraint_field':'range','requires_definition_and_scope_review':True,'candidate_endpoint_classification_required':True,'union_execution_not_supported_by_current_profile':True})}
    for identity, (disposition, reason, details) in withdrawal.items():
        task = repository.extension_status(identity)
        evidence = sorted({eid for op in task['delta']['operations'] for eid in op['evidence_ids']})
        dossier = json.loads((PRIOR/(identity+'.json')).read_text(encoding='utf-8'))
        refs = [{'audit_id': audit['audit_id'], 'round_id': vote['round_id'], 'voter_id': vote['voter_id']}
                for audit in dossier['prior_reviews'] for vote in audit['votes']
                if vote.get('response_status') == 'ok' and vote.get('decision') in {'reject','revise'}]
        if identity == 'ext_0813e33a152048bc87c8fb055e1b0507':
            evidence.append('ev_8abbec1dad96559171c2')
        plans.append(dict(extension_id=identity, expected_row_version=task['row_version'],
            action='withdraw', reason=reason, evidence_ids=sorted(set(evidence)),
            resolution={'disposition':disposition, 'independent_approval':False, **details},
            review_references=refs))

    identity = 'ext_4a9a96a9450145e596202e196fd0c124'
    task = repository.extension_status(identity)
    base = load_release(RELEASES/task['base_release_id'])
    items = catalog(base.ontology)
    if items[task['delta']['operations'][0]['target']]['label'] != '弹丸速度':
        raise RuntimeError('the mistaken alias target changed; re-investigate')
    if any(item.get('label') == '风速' for item in items.values()):
        raise RuntimeError('an exact existing alias owner is now available; use an explicit reuse investigation')
    evidence = sorted({eid for op in task['delta']['operations'] for eid in op['evidence_ids']})
    plans.append(dict(extension_id=identity, expected_row_version=task['row_version'], action='replace',
        reason='原别名错误地挂到另一个速度术语。完整目录未包含原文所指术语，改为最小的新类草案及原文别名，不猜父类；全局别名适用范围仍需原委员会审核。',
        evidence_ids=evidence, operations=[{'kind':'add_class','target':None,
            'changes':{'label':'风速','definition':'原文记作 W 的风的速度，在该参考系叙述中称为牵连速度。',
                       'aliases':['牵连速度'],'label_language':'zh','definition_language':'zh','alias_language':'zh'},
            'evidence_ids':evidence}], resolution={'disposition':'correct_alias_owner_with_minimal_new_class',
                'previous_target_id':task['delta']['operations'][0]['target'],
                'contextual_alias_scope_requires_independent_review':True,'independent_approval':False}))

    identity = 'ext_f9b004e2df2c4613aa4d060302238889'
    task = repository.extension_status(identity)
    exact = [item['id'] for item in items.values() if item['schema_kind']=='class' and item.get('label')=='气压']
    if len(exact) != 1:
        raise RuntimeError('unique exact source endpoint class required')
    operations = deepcopy(task['delta']['operations'])
    for op in operations:
        op['target'] = None
        op['changes']['domain'] = exact
    evidence = sorted({eid for op in operations for eid in op['evidence_ids']})
    plans.append(dict(extension_id=identity, expected_row_version=task['row_version'], action='replace',
        reason='修正草案中错误引用的 domain 正式 ID，指向原文明确命名且已存在的类。候选本身缺失的 head_type 保持 unknown，不能由属性 domain 反推填入。',
        evidence_ids=evidence, operations=operations,
        resolution={'disposition':'correct_exact_named_endpoint_id','candidate_endpoint_classification_required':True,
                    'reuse_schema_ids':exact,'independent_approval':False}))

    identity = 'ext_05b93d2eaa3c4134a199e02948ddc105'
    task = repository.extension_status(identity)
    dossier = json.loads((PRIOR/(identity+'.json')).read_text(encoding='utf-8'))
    refs = [{'audit_id':audit['audit_id'],'round_id':vote['round_id'],'voter_id':vote['voter_id']}
            for audit in dossier['prior_reviews'] for vote in audit['votes']
            if vote.get('response_status')=='ok' and vote.get('decision')=='revise']
    evidence = sorted({eid for op in task['delta']['operations'] for eid in op['evidence_ids']})
    plans.append(dict(extension_id=identity, expected_row_version=task['row_version'], action='annotate',
        reason='保留原有效 revise 票。其逐项检查均为 pass/not_applicable，但 requested_context 和 suggested_changes 均为空，只有 requested_context_invalid 原因；缺少可执行建议，不能擅自解释为批准或通过无关改动重投。',
        evidence_ids=evidence, review_references=refs,
        resolution={'disposition':'reviewer_clarification_required','resolved':False,
                    'requested_clarification':'请原审核员给出具体修改点或合法的 query/scope/limit 上下文请求。',
                    'original_frozen_delta_and_valid_votes_preserved':True,'independent_approval':False}))
    for item in plans:
        item.update(repair_key='draft-checks-20261030:'+item['extension_id'], actor='codex_local_draft_repair')
    return plans


def verify_current_drafts(repository, identities):
    results=[]
    for identity in identities:
        task=repository.extension_status(identity)
        row={'extension_id':identity,'task_row_version':task['row_version'],
             'delta_revision':task['delta_revision'],'has_active_delta':bool(task.get('delta')),
             'resolution':task.get('draft_repair_resolution')}
        if task.get('delta'):
            evidence,documents,candidates,_=load_sources(repository,task)
            runtime=SimpleNamespace(repository=repository,base=load_release(RELEASES/task['base_release_id']),
                documents=documents,candidates=candidates,state={'task':task,'delta':task['delta'],
                'evidence':evidence,'checks':{},'validation':None})
            tools=OntologyTools(runtime)
            row['checks']={'structure':tools.validate_schema_structure(),
                'impact':tools.analyze_schema_impact(),'competency':tools.run_competency_checks()}
        results.append(row)
    return results


def main(apply=False):
    production=ROOT/'runs/v3/review.sqlite3'
    saved=save_backup(production,'before_apply' if apply else 'before_dry')
    database=production if apply else OUT/(saved.stem+'_working.sqlite3')
    if not apply:
        shutil.copy2(saved,database)
    repo=ReviewRepository(database)
    if apply:
        validation=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
        if not validation['successful']:
            raise RuntimeError('successful isolated regression required')
        for relative,digest in validation['source_hashes'].items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('source changed since validation: '+relative)
        dry=json.loads((OUT/'dry_run_receipt.json').read_text(encoding='utf-8'))
        if not dry['immutable_tables_preserved']:
            raise RuntimeError('dry-run preservation evidence required')
        plans=json.loads((OUT/'repair_plan.json').read_text(encoding='utf-8'))
    else:
        plans=plan(repo)
        (OUT/'repair_plan.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2),encoding='utf-8')
    before,projection=fingerprints(repo)
    with repo.connect() as connection:
        old_task_rows={r[0]:r[1] for r in connection.execute('SELECT extension_id,task_json FROM ontology_extension_tasks')}
    receipts=[]
    for item in plans:
        receipt=repair_draft(repo,RELEASES,**item)
        receipts.append(receipt)
        print(json.dumps({'extension_id':item['extension_id'],'action':item['action'],'reused':receipt['reused']},ensure_ascii=False),flush=True)
    ids=[p.stem for p in sorted(PRIOR.glob('ext_*.json'))
         if json.loads(p.read_text(encoding='utf-8')).get('existing_delta')]
    checked=verify_current_drafts(repo,ids)
    after,_=fingerprints(repo,projection)
    changed=[table for table in before if before[table]!=after[table]]
    if set(changed)-{'ontology_extension_tasks'}:
        raise RuntimeError('unexpected mutation outside draft tasks: '+str(changed))
    with repo.connect() as connection:
        new_task_rows={r[0]:r[1] for r in connection.execute('SELECT extension_id,task_json FROM ontology_extension_tasks')}
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
    changed_ids=sorted(identity for identity in old_task_rows if old_task_rows[identity]!=new_task_rows[identity])
    if changed_ids!=sorted(item['extension_id'] for item,receipt in zip(plans,receipts) if not receipt['reused']):
        raise RuntimeError('changed task set does not match explicit repair plan')
    (OUT/('applied_details.json' if apply else 'dry_details.json')).write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/('after_checks.json' if apply else 'dry_after_checks.json')).write_text(json.dumps(checked,ensure_ascii=False,indent=2),encoding='utf-8')
    active=[row for row in checked if row['has_active_delta']]
    result={'mode':'apply' if apply else 'dry_run','backup':str(saved),'database':str(database),
        'explicit_tasks_changed':len(changed_ids),'actions':dict(Counter(item['action'] for item in plans)),
        'prior_drafts_checked':len(checked),'active_drafts_remaining':len(active),
        'active_structure_statuses':dict(Counter(row['checks']['structure']['status'] for row in active)),
        'active_competency_statuses':dict(Counter(row['checks']['competency']['status'] for row in active)),
        'active_trigger_statuses':dict(Counter(check['status'] for row in active
            for check in row['checks']['competency']['checks'] if check['id'].startswith('trigger_'))),
        'immutable_tables_preserved':True,'changed_tables':changed,'integrity_check':integrity,
        'external_model_calls':0,'source_candidates_modified':False,'ontology_published':False,
        'valid_votes_unchanged':before['ontology_votes']==after['ontology_votes'],
        'original_proposals_unchanged':before['ontology_proposals']==after['ontology_proposals'],
        'request_history_unchanged':before['request_attempts']==after['request_attempts'],
        'case_budgets_unchanged':before['review_cases']==after['review_cases'],
        'remaining_obligations':['independent_semantic_review_of_replacements',
            'explicit_remodeling_of_withdrawn_constraint_drafts','candidate_representation_or_type_review',
            'semantic_competency_question_review','clarification_of_original_revise_vote']}
    (OUT/('application_receipt.json' if apply else 'dry_run_receipt.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
