"""Version the user's DEV-007/011 label corrections and DEV-022 subject request."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from review_agent.contracts import sha256_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_corrections_20261103'
ANALYSIS = ROOT / 'outputs/dev30_analysis_20261103'
OUT.mkdir(parents=True, exist_ok=True)
BOOK = ROOT / 'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx'
old = json.loads((ANALYSIS/'annotations_scoped_v3.json').read_text(encoding='utf-8'))
book_hash = hashlib.sha256(BOOK.read_bytes()).hexdigest(); assert book_hash == old['scope']['source_workbook_sha256']
event_path = OUT/'human_correction_event.json'
if event_path.exists():
    event = json.loads(event_path.read_text(encoding='utf-8'))
else:
    event = {'recorded_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
             'user_reply_original': '007和011标注错误。修正主语',
             'source_workbook_sha256': book_hash,
             'label_corrections': [
                 {'sample_id': sid, 'field': field, 'before': before, 'after': after}
                 for sid in ('DEV-007','DEV-011')
                 for field,before,after in [('来源支持','反驳','支持'),('事实表述正确','否','是')]],
             'subject_correction': {'sample_id':'DEV-022','before':'弹丸质心运动微分方程组','after':'矢量方程（1-56）',
                                    'intended_edit_confirmed':True,'new_formal_mapping_approved':False},
             'original_workbook_modified': False, 'production_admission_authorized': False}
    event['event_hash'] = sha256_json(event)
    event_path.write_text(json.dumps(event,ensure_ascii=False,indent=2),encoding='utf-8')
new = deepcopy(old); scope = new['scope']; scope['previous_scope_hash'] = scope.pop('scope_hash')
scope['recorded_at_beijing'] = event['recorded_at_beijing']
scope['item_specific_overrides'].update({
    'DEV-007': {'source_support':'supported','factual_judgment':True,'label_correction_event':event['event_hash']},
    'DEV-011': {'source_support':'supported','factual_judgment':True,'label_correction_event':event['event_hash']},
    'DEV-022': {'subject_correction':'矢量方程（1-56）','original_subject_error_confirmed':True,
                'old_source_support_gold_superseded':True,'new_formal_mapping_approved':False,'label_correction_event':event['event_hash']}})
scope['scope_hash'] = sha256_json(scope)
for row, raw in zip(new['annotations'], old['annotations']):
    row['confirmed_scope_hash'] = scope['scope_hash']
    row['human_labels_effective'] = deepcopy(row['human_labels_original'])
    row['effective_human_source_support'] = row['source_support_as_filled']
    if row['sample_id'] in {'DEV-007','DEV-011'}:
        row['human_labels_effective'].update(来源支持='支持',事实表述正确='是')
        row.update(effective_human_source_support='supported', post_suggestion_factual_judgment=1,
                   original_candidate_factual_gold=True, label_scope_status='user_corrected_label')
        row['label_correction_event_hash'] = event['event_hash']
    if row['sample_id'] == 'DEV-022':
        row.update(label_scope_status='user_confirmed_subject_revision', effective_human_source_support=None,
                   original_subject_error_confirmed=True, original_candidate_factual_gold=False,
                   original_source_label_eligible_for_scoring=False,
                   source_label_exclusion_reason='用户确认原主语有误；原来源支持单元格保留，但不再作为原候选支持真值。')
        row['subject_revision_request'] = event['subject_correction']
        row['post_revision_evaluation_target'] = {'status':'user_confirmed_subject_edit_pending_full_revision_review',
            'content_hash':None,'human_overall_gold':None,'revision_artifact_directory':str(OUT)}
        result_file = OUT/'DEV-022_correction_result.json'
        if result_file.exists():
            result = json.loads(result_file.read_text(encoding='utf-8'))
            assert sha256_json(result['candidate']) == result['candidate_content_hash']
            assert result['candidate']['triple']['head'] == event['subject_correction']['after']
            row['post_revision_evaluation_target'].update(
                status='user_applied_source_supported_formal_mapping_pending',
                content_hash=result['candidate_content_hash'], revision=result['candidate']['revision'],
                source_support_result=result['source_support_result'],
                workflow_state=result['workflow_state'], production_applied=False,
                new_formal_mapping_approved=False)
    assert row['human_labels_original'] == raw['human_labels_original']
(ANALYSIS/'annotations_scoped_v4.json').write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding='utf-8')
patches = [{'sheet':'人工标注','cell':cell,'before':before,'after':after} for cell,before,after in (
    ('E12','反驳','支持'),('L12','否','是'),('E16','反驳','支持'),('L16','否','是'))]
patches += [
    {'sheet':'人工标注','cell':'M27','before':None,'after':'主语改为“矢量方程（1-56）”；在前述基本假设条件下，矢量方程（1-56）投影于直角坐标系。'},
    {'sheet':'人工标注','cell':'N27','before':None,'after':'用户确认修正主语。B列保留冻结原候选；E列原“支持”不再作为原候选支持真值。新稿类型、关系映射及审核状态另行记录。'}]
(OUT/'workbook_patches.json').write_text(json.dumps({'source_workbook':str(BOOK),'source_sha256':book_hash,
    'output_workbook':str(OUT/'第一章30条开发样本_标注校正版.xlsx'),'patches':patches},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'labels_corrected':['DEV-007','DEV-011'],'subject_revision_requested':'DEV-022',
                  'scope_hash':scope['scope_hash'],'original_labels_preserved':True},ensure_ascii=False))
