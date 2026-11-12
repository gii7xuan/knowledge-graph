"""Package the assistant's explicit per-case decisions; do not generate human truth."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from document.hierarchy_index import load_document_index
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import AXES, validate_annotation
from evaluation.review_followup import digest, read, write, jsonl
from review_agent.contracts import sha256_json
from ontologyBuild.release_service import load_release
from schema.relation_registry import RelationRegistry

OUT=Path(__file__).resolve().parent
SOURCE=ROOT/'outputs/dev30_next_20261111_verified/annotation_packets'
ORIGINAL=ROOT/'outputs/dev30_v8_full_20261111'
LABELS={'supported':'支持','contradicted':'与原文矛盾','insufficient':'证据不足','unresolved':'待核验',
    'standard':'规范','lossless_normalization':'需无损规范化','semantic_revision':'需语义修订',
    'context_incomplete':'映射材料不全','conflict':'映射冲突','include':'收录','exclude':'排除','unclear':'范围待定',
    'wait_mapping':'等待映射','manual_review':'定向复核','revise':'修订后重审'}


def check(status,reason,refs):
    return {'status':status,'reason':reason,'evidence_ids':deepcopy(refs)}


def expression_title(row):
    return ('字段需整改，方案待核' if row['four_axes']['expression']=='unresolved' and
        row['expression_standard_compliance']=='noncompliant' else LABELS[row['four_axes']['expression']])


def main():
    decisions=read(OUT/'review_decisions.json')
    assert len(decisions)==30 and {r['sample_id'] for r in decisions}=={f'DEV-{i:03}' for i in range(1,31)}
    protected={str(p):digest(p) for p in SOURCE.glob('DEV-*.json')}
    protected[str(ORIGINAL/'review.sqlite3')]=digest(ORIGINAL/'review.sqlite3')
    human=ROOT/'outputs/dev5_v8_human_20261110_150238/annotations.json'
    protected[str(human)]=digest(human)
    packets={p.stem:read(p) for p in SOURCE.glob('DEV-*.json')}
    first=packets['DEV-001']['candidate']
    repo=ReadOnlyRepository(ORIGINAL/'review.sqlite3')
    run=repo.get_run(first['run_id'])
    doc=load_document_index(run['document_path'],first['source_index_hash'],require_verified=True)
    canonical=doc['source_snapshot']['canonical_text']
    release=load_release(first['ontology_release_path'])
    registry=RelationRegistry(release.ontology)
    now=datetime.now(timezone(timedelta(hours=8))).isoformat()
    annotations=[];supplements=[];anchor_checks=0;reports=[]
    for note in decisions:
        sid=note['sample_id'];p=packets[sid];c=p['candidate'];t=c['triple'];items=p['evidence_bundle']['items']
        material={k:v for k,v in p.items() if k not in {'packet_id','system','sample_id','human_and_model_same_packet'}}
        assert p['packet_id']=='packet_'+sha256_json(material),sid
        assert p['binding']['candidate_content_hash']==sha256_json(c)
        for e in items:
            assert canonical[e['start']:e['end']]==e['quote'],(sid,e['evidence_id'])
            assert e['anchor_valid'] is True
            anchor_checks+=1
        available={e['evidence_id'] for e in items}
        assert set(note['evidence_ids'])<=available,sid
        extra=[]
        if sid=='DEV-019':
            start=canonical.index('对于地炮，');end=canonical.index('诸元即可。',start)+len('诸元即可。')
            e={'document_id':c['document_id'],'document_version':c['document_version'],
                'source_index_hash':c['source_index_hash'],'section_id':c['section_id'],
                'start':start,'end':end,'quote':canonical[start:end],'offset_basis':'canonical_document',
                'anchor_valid':True,'kind':'annotation_supplement_not_seen_by_original_votes'}
            e['context_id']='ctx_'+sha256_json(e)[:24];extra=[e]
            supplements.append({'sample_id':sid,'base_packet_id':p['packet_id'],'contexts':extra,
                'base_source_label':note['base_source'],'expanded_source_label':note['source'],
                'old_votes_saw_this_evidence':False,'new_review_round_required':True})
        evidence_refs=note['evidence_ids']+[e['context_id'] for e in extra]
        mapping=note.get('mapping_override','context_incomplete')
        guard=p['ontology_check'];traces=p['mapping_preflight'];assigned=t.get('relation_id')
        if mapping=='conflict':
            assert guard['range_status']=='fail' and guard['hard_failure'] is True
            mapping_reason=('正式ID '+assigned+' 的range限定为C_aef809afb1c169b7ed7f（科氏加速度），'
                '实际尾类型为C_a1bfa7db3298783a8f65（地面曲率），冻结类层级无满足路径。'
                '关系定义缺失与真实类型冲突并存；缺定义不能覆盖冲突。')
        else:
            mapping_reason=('存在候选正式ID但关系定义正文缺失，不能由名称和结构约束推断语义等价。' if assigned else
                '当前没有已分配且经正式定义与约束确认的关系；召回候选不等于等价映射，无匹配定义也不证明本体缺口。')
            if any(e.get('missing_information') for e in traces.get('entities',{}).values()):
                mapping_reason+='另有身份/类型材料缺项；原文中可识别的指称不等同于正式身份已登记。'
            if sid=='DEV-022':mapping_reason+='而且形式domain通过也不能消除源文本投影主体错误。'
            if sid=='DEV-026':mapping_reason+='literal尾项遗留tail_type=弹丸，不能将句中实体类型当成整个结果命题的类型。'
        axes={'source':note['source'],'expression':note['expression'],'mapping':mapping,'scope':note['scope']}
        assert all(axes[k] in AXES[k] for k in axes)
        base_axes={**axes,'source':note.get('base_source',note['source'])}
        rationale={'source':note['source_reason'],'expression':note['expression_reason'],
                   'mapping':mapping_reason,'scope':note['scope_reason']}
        bundle={'version':'ai_annotation_material_v1','base_packet':deepcopy(p),'supplementary_contexts':extra,
                'supplemental_material_never_rebinds_original_model_votes':True}
        bundle_id='ai_packet_'+sha256_json(bundle)
        bundle['annotation_packet_id']=bundle_id
        write(OUT/'annotation_materials'/f'{sid}.json',bundle)
        nondecidable={'insufficient','unresolved','context_incomplete','unclear'}
        axis_review={k:{'status':v,'decidable':v not in nondecidable,'reason':rationale[k],
            'evidence_ids':deepcopy(evidence_refs),'confidence':note['confidence'] if k!='mapping' else 'high',
            'blocker_type':('input_material_missing' if k=='mapping' and v=='context_incomplete' else
                'constraint_conflict' if k=='mapping' and v=='conflict' else
                'semantic_dispute' if sid=='DEV-017' and k in {'source','expression'} else
                'input_material_missing' if sid=='DEV-005' and k in {'source','expression','scope'} else
                'field_policy_or_encoding' if k=='expression' and v=='unresolved' else
                'candidate_semantic_error' if v in {'semantic_revision','contradicted'} else None)} for k,v in axes.items()}
        # A missing executable transformation does not make an observed field defect unknowable.
        compliance=('compliant' if note['expression']=='standard' else
            'not_determinable' if sid in {'DEV-005','DEV-017'} else 'noncompliant')
        axis_review['expression'].update(standard_compliance=compliance,
            decidable=compliance!='not_determinable',
            remediation_plan_verified=sid=='DEV-020',
            interpretation='Canonical unresolved may mean a known field defect with an unverified remediation; it does not certify the expression as standard.')
        axis_review['mapping']['missing_material']=deepcopy(traces.get('blocking_information',[]))
        checks={
            'triple_support':check(note['source'],note['source_reason'],evidence_refs),
            'entity_reference':check('unknown' if sid in {'DEV-005','DEV-028'} else 'pass',
                '需澄清函数或模型身份。' if sid in {'DEV-005','DEV-028'} else '已结合完整句子、相邻指代或公式名称核对原文指称；正式身份另审。',evidence_refs),
            'relation_semantics':check('fail' if sid=='DEV-022' else 'unknown' if sid in {'DEV-005','DEV-017','DEV-021'} else 'pass',
                note['source_reason'] if sid in {'DEV-005','DEV-017','DEV-022'} else note['expression_reason'] if sid=='DEV-021' else '按原文关系的强弱和作用对象核对，未用本体标签替换源语义。',evidence_refs),
            'relation_direction':check('fail' if sid=='DEV-022' else 'unknown' if sid in {'DEV-005','DEV-017'} else 'pass',
                '投影的动作主体/结果被混用。' if sid=='DEV-022' else '统称/包含含义未定，暂不能确认。' if sid in {'DEV-005','DEV-017'} else '当前读法的主尾方向与所引原文相符。',evidence_refs),
            'qualifiers':check('fail' if sid in {'DEV-012','DEV-020','DEV-021','DEV-028'} else 'unknown' if note['expression']=='unresolved' else 'pass',
                note['expression_reason'] if note['expression']!='standard' else '已核对条件、频率、极性、模态及限定附着；集合或近似未被擅自改写。',evidence_refs),
            'evidence_sufficiency':check('unknown' if note['source'] in {'insufficient','unresolved'} else 'pass',
                '当前材料不足以完成整个命题的肯定/否定裁决。' if note['source'] in {'insufficient','unresolved'} else
                '足以识别反驳，证据充足不等于候选正确。' if sid=='DEV-022' else
                '仅在新增出处后的扩展材料下充分；原包不足。' if extra else '具备可定位的正文/公式组合，不以标题或图片链接单独充当支持。',evidence_refs),
            'mapping':check(mapping,mapping_reason,evidence_refs),
            'knowledge_scope':check(note['scope_category'],note['scope_reason'],evidence_refs),
            'counterevidence':check('fail' if sid=='DEV-022' else 'unknown' if sid in {'DEV-017','DEV-028'} else 'pass',
                '原文明确将矢量方程作为投影输入，构成当前候选的有效反证。' if sid=='DEV-022' else
                '保留关系含义或模型身份争议，不能把它写成已排除的反例。' if sid in {'DEV-017','DEV-028'} else
                '在本次可用材料中未发现直接反证；此项不产生正向支持，亦不保证科学正确。',evidence_refs)}
        known_block=note['scope']=='exclude' or note['needs_revision'] is True or mapping=='conflict' or note['source']=='contradicted'
        row={'version':'assistant_four_axis_annotation_v1','sample_id':sid,'packet_id':p['packet_id'],
            'binding':deepcopy(p['binding']),'annotation_packet_id':bundle_id,
            'annotation_material_hash':bundle_id.removeprefix('ai_packet_'),'status':'reviewed_ai',
            'annotated_by':'ai','reviewer':'Codex assistant','annotated_at':now,'human_verified':False,
            'eligible_as_human_ground_truth':False,'saw_model_conclusions':True,
            'prior_context_exposure':'This assistant saw prior run summaries and a few failure receipts before this annotation task; this is not a blinded independent study.',
            'label_method':'Per-case assistant review of source text, frozen ontology and field/scope rules; no calls to external model services in this task.',
            'four_axes':axes,'base_packet_four_axes':base_axes,'axis_review':axis_review,
            'expression_standard_compliance':compliance,
            'checks':checks,'evidence_ids':note['evidence_ids'],'supplementary_context_ids':[e['context_id'] for e in extra],
            'admission_eligible':False if known_block else None,'ready_for_admission':False,
            'needs_revision':note['needs_revision'],'mapping_correct':False if mapping=='conflict' else None,
            'correct_disposition':note['disposition'],'rule_ids':note['rules'],'issue_codes':note['issues'],
            'suggested_next_action':note['suggestion'],'revision_applied':False,
            'normalization_plan_ids_assessed':[x['plan_id'] for x in p['field_standardization']['plans']] if sid=='DEV-020' else [],
            'ontology_reference':{'release_id':c['ontology_release_id'],'version':c['ontology_version'],
                'assigned_relation':deepcopy(registry.properties.get(assigned)) if assigned else None,
                'assigned_endpoint_classes':{k:deepcopy(release.ontology['classes'].get(t.get(k))) for k in ('head_type','tail_type')},
                'frozen_guard':deepcopy(guard),'recalled_relation_ids':[r['id'] for r in traces['relation_candidates']],
                'ontology_missing_definition_is_not_absence_proof':True},
            'unviewed_material':['Remote image pixels were not fetched; labels rely on the cited text/formulas.'],
            'scientific_validity':'not_independently_verified_beyond_supplied_material',
            'note':'；'.join(rationale[k] for k in ('source','expression','mapping','scope'))}
        # Production human-quality validation must continue rejecting AI-origin labels.
        try:validate_annotation(row,p)
        except ValueError:pass
        else:raise AssertionError('AI-origin label was accepted as human truth')
        annotations.append(row)
        refs={e['evidence_id']:e for e in items}
        report=[f'# {sid}：AI逐项复核','',f"来源候选revision={c['revision']}；原包 {p['packet_id']}。",
            '本文件由AI逐条判断，未经人工确认；评审者此前见过本轮摘要，不属于盲标。','',
            '## 当前候选','', '```json',json.dumps(t,ensure_ascii=False,indent=2),'```','',
            '|维度|结论|可判定|依据|','|---|---|---|---|',
            *[f"|{k}|{LABELS[v]}|{'是' if axis_review[k]['decidable'] else '否/材料或语义待补'}|{rationale[k]}|" for k,v in axes.items()],
            '',f"表达规范符合性：{compliance}；修复计划已核验：{'是，仅DEV-020的既有计划' if sid=='DEV-020' else '否/不适用'}。",
            f"建议处置：{LABELS[note['disposition']]}。{note['suggestion']}",'',
            '## 核心证据','']
        for eid in note['evidence_ids']:
            e=refs[eid];report += [f"- {eid}，原文字符 [{e['start']}, {e['end']})：",'', '> '+e['quote'].replace('\n','\n> '),'']
        if extra:
            report += ['## 与旧轮隔离的补充原文','']
            for e in extra:report += [f"- {e['context_id']}，[{e['start']}, {e['end']})：{e['quote']}",'']
            report += ['原包source=insufficient；扩展原文后source=supported。新增材料不得用于声称原票已获得充分证据。','']
        report += ['## 九项检查','',*['- '+k+'：'+v['status']+'。'+v['reason'] for k,v in checks.items()],
            '', '完整材料与精确绑定见 [对应标注材料](../annotation_materials/'+sid+'.json)。','']
        target=OUT/'case_reviews'/f'{sid}.md';target.parent.mkdir(exist_ok=True)
        target.write_text('\n'.join(report),encoding='utf-8')
        reports.append({'sample_id':sid,'file':str(target.relative_to(OUT))})
    jsonl(OUT/'annotations_ai.jsonl',annotations);jsonl(OUT/'supplementary_contexts.jsonl',supplements)
    # Freeze judgments before comparing them with the selected model outcomes.
    write(OUT/'annotation_manifest.json',{'version':'assistant_annotation_manifest_v1','annotated_by':'ai',
        'created_at':now,'population':30,'axis_labels':120,'source':str(SOURCE),
        'decision_notes_sha256':digest(OUT/'review_decisions.json'),'annotation_sha256':digest(OUT/'annotations_ai.jsonl'),
        'production_human_truth_eligible':False,'source_hashes':protected,'supplemented_cases':['DEV-019'],
        'blind_annotation':False,'new_external_model_calls':0,'production_written':False})
    model={c['sample_id']:c['outcome'] for c in read(ORIGINAL/'smoke_report.json')['cases']}
    comparisons=[]
    for a in annotations:
        o=model[a['sample_id']];base=a['base_packet_four_axes']
        comparisons.append({'sample_id':a['sample_id'],'system_axes':{k:o['dimension_results'][k]['status'] for k in AXES},
            'ai_axes_on_original_packet':base,'ai_axes_with_supplement':a['four_axes'],
            'differing_axes':[k for k in AXES if base[k]!=o['dimension_results'][k]['status']],
            'system_recommendation':o['recommendation'],'ai_suggested_disposition':a['correct_disposition'],
            'human_accuracy_metric':False,'same_material_comparison':True,
            'supplemented_judgment_excluded_from_same_material_comparison':bool(a['supplementary_context_ids'])})
    jsonl(OUT/'system_comparison_after_annotation.jsonl',comparisons)
    summary={'population':30,'origin':'ai_not_human','human_labels_created':0,
        'axis_counts':{k:dict(Counter(a['four_axes'][k] for a in annotations)) for k in AXES},
        'axis_decidable_counts':{k:sum(a['axis_review'][k]['decidable'] for a in annotations) for k in AXES},
        'expression_standard_compliance_counts':dict(Counter(a['expression_standard_compliance'] for a in annotations)),
        'base_packet_source_counts':dict(Counter(a['base_packet_four_axes']['source'] for a in annotations)),
        'disposition_counts':dict(Counter(a['correct_disposition'] for a in annotations)),
        'model_axis_confusion_diagnostic':{k:[{'system':s,'ai':v,'count':n} for (s,v),n in
            Counter((c['system_axes'][k],c['ai_axes_on_original_packet'][k]) for c in comparisons).items()] for k in AXES},
        'accuracy_precision_recall':None,'quality_metric_reason':'AI review with prior model-summary exposure; no independent human truth.',
        'sources_verified':anchor_checks,'new_external_model_calls':0}
    write(OUT/'summary.json',summary)
    lines=['# 30条逐项复核标签（AI标注）','',
        '30条均已逐项阅读候选、全部文本证据、相关本体定义/约束及冻结字段/范围标准，形成四维标签和九项检查。'
        '这些是AI复核结果，未经人工确认，未导入人工真值、投票、生产准入或旧标签。评审者此前见过本轮摘要及若干失败回执，因此不宣称盲标或独立质量评测。',
        '', '|维度|支持/合规或可作结论|材料或语义待定|','|---|---|---|',
        '|原文支持（含单独绑定的补充材料）|25支持、1与原文矛盾|3证据不足、1语义争议|',
        '|表达规范|14规范、14需处理|2不能裁决|',
        '|正式映射|1确认冲突|29缺正式定义等材料|',
        '|收录范围|26收录、3排除|1待定|','',
        '表达需处理的14条分为4条语义修订、1条可按既有计划无损规范化、9条已定位字段缺陷但执行方案待核。后9条的兼容状态仍为unresolved；仅DEV-005/017的表达符合性本身不能裁决。原证据包的source统计为24支持、1矛盾、4证据不足、1争议；DEV-019补充原句后的支持不回写原包。',
        f"可判定项：原文{summary['axis_decidable_counts']['source']}/30、表达{summary['axis_decidable_counts']['expression']}/30、映射{summary['axis_decidable_counts']['mapping']}/30、范围{summary['axis_decidable_counts']['scope']}/30；不可判定项逐条给出理由，不当作错误。",'',
        '|样本|原文支持|表达|映射|范围|建议|','|---|---|---|---|---|---|',
        *[f"|[{a['sample_id']}](case_reviews/{a['sample_id']}.md)|{LABELS[a['four_axes']['source']]}|{expression_title(a)}|{LABELS[a['four_axes']['mapping']]}|{LABELS[a['four_axes']['scope']]}|{LABELS[a['correct_disposition']]}|" for a in annotations],
        '', '## 关键判定及其边界','',
        '- DEV-019：原包引用的证据不包含地炮/常常，只能标证据不足。冻结原文补充句另行绑定后才标支持；不得据此回写旧票或将旧轮判定当作已具备充分证据。',
        '- DEV-028：现有关系ID的range是科氏加速度而尾项是地面曲率，已确认真实约束冲突。其余29条缺正式关系定义等材料，映射正确性不可判定，不能全记错或凭同名标签记对。',
        '- DEV-022：投影主体应是矢量方程而非结果方程组，是明确原文冲突；该过程候选同时属于政策排除的推导步骤。',
        '- DEV-017：统称是否足以表达具有部件取决于正式关系语义，当前保留争议。不能为了证明修订有用而预设必须改。',
        '- DEV-020：现有两个精确计划能保留总是及稳定条件，只支持无损规范化判断，不证明修订已经应用或成功。',
        '- DEV-001/010/011/023/024：程度、属性量值或完整条件命题的字段职责需处理；内容未因此直接判假。DEV-006/018的unknown极性是表达完整性缺项。',
        '- DEV-003/014/030：领域概念定义、几何关系或模型分类不因使用符号或定义字样而自动排除。DEV-002/022为过程关系、DEV-029为局部符号约定，按当前政策排除。',
        '- DEV-016保留集合与超声速限定，没有执行分配式拆分；不能仅凭复合尾项就判语义错误。',
        '- DEV-018的文本支持和严格科学比例不是同一结论；DEV-027的符号约定也未在此独立作物理正确性认证。',
        '', '## 标准及输出','',
        '原文维度按全文关系、指称、方向和限定判断；表达维度按review_fields_v1，不把任意建议伪装成已有确定性计划；映射需要正式语义、身份和约束，真实冲突优先于缺材料；范围按knowledge_scope_v2的通用概念/条件知识与局部定义/推导/示例边界。',
        '', '- `annotations_ai.jsonl`：30条四维、九项、依据、可判定性、建议及精确版本绑定。',
        '- `case_reviews/`：逐条可读报告；`annotation_materials/`：原材料及补充材料。',
        '- `supplementary_contexts.jsonl`：DEV-019新增原文位置与旧轮隔离说明。',
        '- `system_comparison_after_annotation.jsonl`：标签冻结后再对照系统，使用原包标签比较，扩展标签不冒充同输入结果。',
        '- `summary.json`：标签覆盖和描述性分歧计数，不给AI标签冒充人工后的准确率。',
        '', '不能把本次AI标签直接导入现有人工质量评价入口。后续人工可逐维确认并生成具名的新人工绑定；未确认前生产人评覆盖仍为0/30。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    after={p:digest(Path(p)) for p in protected};assert protected==after
    write(OUT/'verification.json',{'all_30_cases_present':True,'all_120_axis_labels_valid':True,
        'all_original_citations_belong_to_their_packet':True,'verified_original_anchor_count':anchor_checks,
        'supplementary_context_anchored_and_separate':True,'ai_labels_rejected_by_human_truth_validator':True,
        'original_packets_database_and_old_human_labels_unchanged':True,'new_external_model_calls':0,
        'annotation_manifest_sha256':digest(OUT/'annotation_manifest.json')})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
