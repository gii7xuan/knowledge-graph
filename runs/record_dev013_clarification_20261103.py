"""Record the user's item-specific clarification without changing the workbook."""
from copy import deepcopy
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('dev4', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
frozen = pilot.prepare()
target = next(i for i in frozen['items'] if i['sample_id'] == 'DEV-013')
clarification = {
    'sample_id': 'DEV-013', 'recorded_at_beijing': pilot.datetime.now().astimezone().isoformat(),
    'user_reply_original': '候选写的是摩擦阻力与表面粗糙度有关，缺少“弹丸”',
    'scope': '原候选缺少宾语所属对象“弹丸”；这是本条原否定判断的具体依据。',
    'intended_edit': {'tail': '弹丸表面粗糙度'},
    'complete_revision_target': target['complete_statement'],
    'target_content_hash': target['target_content_hash'],
    'intended_edit_confirmed': True,
    'original_negative_label_applies_to_new_target': False,
    'new_target_overall_gold': None,
    'normalization_rule': '保留E/F/L原填写值。针对原候选缺词的否定不能作为完整修订稿的负例，也不自动生成新稿的总体肯定标签。',
    'original_workbook_modified': False,
}
clarification['clarification_hash'] = pilot.sha256_json(clarification)
pilot.dump('DEV-013_human_clarification.json', clarification)

analysis = ROOT / 'outputs/dev30_analysis_20261103'
old = json.loads((analysis / 'annotations_scoped_v1.json').read_text(encoding='utf-8'))
new = deepcopy(old)
scope = new['scope']; scope['previous_scope_hash'] = scope.pop('scope_hash')
scope['item_specific_overrides'] = {'DEV-013': {
    'original_L_negative_reason': '原候选宾语缺少“弹丸”',
    'negative_refers_to': 'original_candidate_wording',
    'post_revision_gold': None,
    'clarification_hash': clarification['clarification_hash'],
}}
scope['recorded_at_beijing'] = clarification['recorded_at_beijing']
scope['scope_hash'] = pilot.sha256_json(scope)
targets = {i['sample_id']: i for i in frozen['items']}
for item, prior in zip(new['annotations'], old['annotations']):
    item['confirmed_scope_hash'] = scope['scope_hash']
    if item['sample_id'] in targets:
        t = targets[item['sample_id']]
        item['post_revision_evaluation_target'] = {
            'status': 'frozen_development_proposal_pending_human_overall_label',
            'content_hash': t['target_content_hash'], 'complete_statement': t['complete_statement'],
            'draft_id': t['draft_id'], 'human_overall_gold': None,
        }
    if item['sample_id'] == 'DEV-013':
        item['post_suggestion_factual_judgment'] = None
        item['negative_label_scope'] = 'original_candidate_missing_projectile_modifier'
        item['human_clarification'] = clarification
        item['label_scope_status'] = 'item_specific_user_clarification_recorded'
    assert item['human_labels_original'] == prior['human_labels_original']
(analysis / 'annotations_scoped_v2.json').write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding='utf-8')
state = json.loads((pilot.OUT / 'review_status.json').read_text(encoding='utf-8'))
pilot.report(frozen, state)
pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
print(json.dumps({'sample_id': 'DEV-013', 'intended_edit_confirmed': True,
                  'scope_hash': scope['scope_hash'], 'raw_workbook_labels_unchanged': True,
                  'new_overall_gold_not_invented': True}, ensure_ascii=False))
