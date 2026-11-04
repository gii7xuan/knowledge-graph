"""Record the user's explicit label scope without altering annotations or candidates."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import json,hashlib,re
from review_agent.contracts import sha256_json

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/dev30_analysis_20261103';SRC=ROOT/'outputs/dev30_20261031'
data=json.loads((SRC/'sample_data.json').read_text(encoding='utf-8'))
original=json.loads((OUT/'annotations_original.json').read_text(encoding='utf-8'))
audit=json.loads((OUT/'annotation_audit.json').read_text(encoding='utf-8'))
workbook=SRC/'第一章30条开发样本_人工填写.xlsx'
assert hashlib.sha256(workbook.read_bytes()).hexdigest()==original['source_workbook_sha256']
assert not any(audit[k] for k in ('structural_issues','frozen_candidate_display_changes','frozen_evidence_changes','frozen_binding_changes','frozen_ontology_reference_changes','live_candidate_version_changes','missing_core_labels','invalid_labels'))
scope={'confirmed_by':'user_reply_20261103','source_workbook_sha256':original['source_workbook_sha256'],
       'overall_statement_column_L':'after_suggested_revision',
       'ontology_compatibility_column_I':'conceptual_expressibility',
       'ontology_gap_column_K':'conceptual_expressibility',
       'knowledge_selection_exclusions':{
           'DEV-028':'原文中只是讲解不同情况下的公式，不是本次任务所需有效知识。',
           'DEV-029':'讲解公式时的假设，不是本次任务所需有效知识。'},
       'relation_mapping_column_H_formal_ID_scope':'not_explicitly_confirmed',
       'user_replies_original':['按建议修改后的表述判断','028在原文中只是讲解不同情况下的公式，不是有效知识；029也不是有效知识','概念层面能够表达']}
scope['scope_hash']=sha256_json(scope)
scope['recorded_at_beijing']=datetime.now(timezone(timedelta(hours=8))).isoformat()
candidates={b['sample_id']:c for b,c in zip(data['bindings'],data['selected_raw_candidates'])}
normalized=[];followup=[]
source_map={'支持':'supported','反驳':'contradicted','证据不足':'insufficient','无法判断':'unknown'}
for a in original['annotations']:
    sid=a['sample_id'];h=a['human_labels_original'];excluded=sid in scope['knowledge_selection_exclusions']
    row={**a,'label_scope_status':'user_clarification_recorded','confirmed_scope_hash':scope['scope_hash'],
         'source_support_as_filled':source_map[h['来源支持']],
         'ontology_concept_expressible':h['本体兼容']=='是',
         'ontology_concept_gap':h['本体覆盖缺口']=='有',
         'post_suggestion_fact_judgment_as_filled':h['事实表述正确'],
         'post_suggestion_factual_judgment':None if excluded else 1 if h['事实表述正确']=='是' else 0 if h['事实表述正确']=='否' else None,
         'knowledge_selection_excluded':excluded,
         'knowledge_selection_exclusion_reason':scope['knowledge_selection_exclusions'].get(sid),
         'original_candidate_overall_gold':None,'current_formal_ID_compatibility_gold':None,
         'formal_relation_ID_mapping_gold':None,'valid_for_kg':None,
         'post_revision_evaluation_target':{'status':'not_frozen','content_hash':None},
         'model_training_authorized':False,'production_admission_authorized':False}
    normalized.append(row)
    if a.get('suggested_revision_original') or h['限定完整']=='否' or h['关系方向正确']=='否':
        followup.append({'sample_id':sid,'candidate_id':a['candidate_id'],'base_revision':a['revision'],
            'base_content_hash':a['candidate_content_hash'],'original_triple':candidates[sid]['triple'],
            'human_suggested_revision_original':a.get('suggested_revision_original'),
            'post_suggestion_fact_judgment_as_filled':h['事实表述正确'],
            'final_revised_statement':None,'final_qualifiers':None,'target_content_hash':None,
            'reason':'修订后评价需要对应完整的表述与限定；原备注按字段片段保留，不自动猜测或提交修改。',
            'original_candidate_changed':False})
locator_ids=[i['sample_id'] for i in audit['clarification_issues'] if i['code']=='authoring_quote_locator_defect']
locator_ids=sorted(set(locator_ids));assert locator_ids==['DEV-004','DEV-011','DEV-012','DEV-014','DEV-025']
summary={'samples':len(normalized),'complete_core_labels':240,'source_counts':audit['counts']['来源支持'],
    'post_suggestion_raw_counts':audit['counts']['事实表述正确'],
    'post_suggestion_factual_judgment_counts':{'yes':sum(r['post_suggestion_factual_judgment']==1 for r in normalized),
        'no':sum(r['post_suggestion_factual_judgment']==0 for r in normalized),
        'not_scored_due_to_knowledge_selection':sum(r['knowledge_selection_excluded'] for r in normalized)},
    'knowledge_selection_excluded':['DEV-028','DEV-029'],'conceptual_ontology_expressible':30,'conceptual_ontology_gap_positive':0,
    'formal_ID_compatibility_gold_available':False,'original_candidate_overall_gold_available':False,
    'revised_statement_followup':[r['sample_id'] for r in followup],
    'locator_presentation_defects':locator_ids,'locator_defects_resolved_by_supplement_only':True,
    'raw_machine_source_vs_human':audit['raw_machine_source_vs_human'],
    'source_workbook_unchanged':True,'production_data_written':False,'model_calls':0,
    'scope_hash':scope['scope_hash'],'original_workbook_sha256':original['source_workbook_sha256']}
for name,value in [('confirmed_annotation_scope.json',scope),('annotations_scoped_v1.json',{'scope':scope,'annotations':normalized}),
                   ('revision_targets_to_finalize.json',followup),('confirmed_scope_summary.json',summary)]:
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

lines=['# 人工标注口径确认与开发对照（2026-11-03）','',
    '已完整读取30条、240项核心标注，全部选项合法。冻结候选、134段原文与坐标、270个本体术语及版本绑定均未改变；30条当前候选版本仍与原冻结版本相同。','',
    '## 用户确认的评价口径','',
    '- L列“事实表述正确”评价建议修改后的表述。原记录25条是、5条否，不能直接当作原候选总体正确率。',
    '- DEV-028、DEV-029不属于本次图谱希望保留的有效知识。按任务取舍记录排除，原“来源支持”和“任务相关”单元格原样保留，不自动判为原文反驳或事实错误。',
    '- 本体兼容和覆盖缺口评价概念层面是否能表达。30条均填概念可表达、无概念缺口，不代表当前正式关系/类型ID和约束已齐全。',
    '- H列保留原判；其中15条未指定正式关系ID却标为映射正确。正式ID映射准确率暂不计算。','',
    '## 已填结果的描述性对照','',
    '| 当前程序来源结果 | 人工支持 | 人工反驳 | 人工证据不足 |','|---|---:|---:|---:|',
    '| 来源支持共识（20条） | 16 | 3 | 1 |',
    '| 来源共识未闭合（10条） | 10 | 0 | 0 |','',
    '这里的未闭合不是拒绝；不能将后10条计为系统错误拒绝。各模型来源字段与程序共识的分项对照见初次核对报告。',
    '人工来源判断共26条支持、3条反驳、1条证据不足。该对照用于开发诊断，尚需结合引文展示问题复核，不能外推全章或充当正式测试集结果。','',
    '## 修订后评价如何落实','',
    '按用户口径，L列中25条为修改后肯定判断。5条否中，028和029记录为任务取舍排除，其余3条保留否定判断；不把排除项强行记为事实错误。',
    '修订后正确性必须绑定修订后完整文本与限定。当前修改建议主要是字段片段，以下4条先进入修订目标整理，不改变生产候选，也不把原版本标签套给新版本：','',
    '| 样本 | 原备注/分项 | 下一步要锁定的内容 |','|---|---|---|',
    '| DEV-004 | 限定完整为否，未给出修改文本 | 缺少的具体条件及最终完整表述 |',
    '| DEV-013 | 建议宾语“弹丸表面粗糙度”，L仍为否 | 完整修订表述、限定和否定判断的依据 |',
    '| DEV-017 | 方向为否，建议关系“由…组成” | 完整主语、关系、宾语及方向，避免只替换关系词后遗漏原意 |',
    '| DEV-020 | 建议关系“具有特征” | 完整属性对象、取值及原有条件/量词，避免改关系时丢失含义 |','',
    '## 主表引文展示修正','',
    '前次主表把最近的定位片段作为直接引文，5条仅显示公式分隔符：DEV-004、011、012、014、025。这是样本导出展示疏漏，完整证据一直在原文证据表中。',
    '本次提供《主表引文补充正文》作为旁附材料；原Excel与人工标签保持不变，不自动作废或更改上述标签。','',
    '## 接下来的实验安排','',
    '先用来源支持、限定和方向这些分项开展开发诊断与同输入对照。修订后质量等完整修订目标冻结后再评价；当前样本的本体概念缺口正例为0，缺口召回率和动态扩展缺口保留收益暂不可计算。',
    '后续标注将DEV-028、029作为任务取舍案例；这次确认只针对这两条，不把“含公式”作为自动排除条件。',
    '本轮没有回写工作台决定、没有发布本体、没有导入图谱、没有训练校准器或调用外部模型。','',
    '原填写文件 SHA-256：'+original['source_workbook_sha256'],
    '确认口径 SHA-256：'+scope['scope_hash'],'']
(OUT/'人工标注口径确认与开发对照.md').write_text('\n'.join(lines),encoding='utf-8')

supp=['# 主表引文补充正文','',
      '本文件补充5条主表只显示公式分隔符的样本。所有摘录均来自已冻结的原文证据，不更改人工标签；排序仅供定位，不预设支持或反驳。','']
for sid in locator_ids:
    t=candidates[sid]['triple'];cjk=re.findall(r'[\u4e00-\u9fff]',t['head']+str(t['tail'])+t['surface_relation'])
    tokens={''.join(cjk[i:i+2]) for i in range(max(0,len(cjk)-1))}
    rows=[r for r in data['evidence_rows'] if r[0]==sid and len(re.findall(r'[\u4e00-\u9fff]',r[7]))>=6]
    def score(r):return sum(token in r[7] for token in tokens)+8*(t['head'] in r[7])+8*(str(t['tail']) in r[7])
    rows.sort(key=lambda r:(-score(r),r[1],r[3]))
    assert rows,'no prose available for '+sid
    supp+=['## '+sid,'','候选：'+t['head']+' → '+t['surface_relation']+' → '+str(t['tail']),
           '候选现有限定：'+json.dumps(t.get('qualifiers'),ensure_ascii=False),'']
    for r in rows[:3]:
        assert data['source_canonical_text'][r[5]:r[6]]==r[7]
        supp+=['章节：'+str(r[4])+'；证据ID：'+r[2]+'；规范全文位置 ['+str(r[5])+', '+str(r[6])+')','',
               '```text',r[7],'```','','相邻上下文：','```text',r[8],'```','']
    supp+=['全部材料仍见原Excel“原文证据”表的同一样本编号。','']
(OUT/'主表引文补充正文.md').write_text('\n'.join(supp),encoding='utf-8')
assert hashlib.sha256(workbook.read_bytes()).hexdigest()==original['source_workbook_sha256']
(OUT/'README.md').write_text('# 当前记录\n\n最新版：人工标注口径确认与开发对照.md。\n\n人工标注核对报告.md及annotation_audit.json保留为用户澄清前的初次检查记录。原填写Excel未改动。\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
