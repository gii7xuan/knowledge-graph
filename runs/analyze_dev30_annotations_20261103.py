"""Read-only audit of user-entered labels, frozen inputs and machine results."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json,zipfile,io,hashlib,re,sqlite3
import xml.etree.ElementTree as ET
from review_agent.contracts import sha256_json
from evaluation.manifest import validate_manifest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_20261031'
WORKBOOK=SOURCE/'第一章30条开发样本_人工填写.xlsx'
OUT=ROOT/'outputs/dev30_analysis_20261103'
OUT.mkdir(parents=True,exist_ok=True)
raw=WORKBOOK.read_bytes();workbook_hash=hashlib.sha256(raw).hexdigest()
baseline=json.loads((SOURCE/'sample_data.json').read_text(encoding='utf-8'))
validate_manifest(baseline['manifest'])
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
def load_workbook():
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        strings=[''.join(t.text or '' for t in item.findall('.//s:t',NS)) for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',NS)] if 'xl/sharedStrings.xml' in z.namelist() else []
        relationships={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        output={}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS):
            target=relationships[sheet.get('{'+REL+'}id')]
            name=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            cells={};formulas={}
            for c in ET.fromstring(z.read(name)).findall('s:sheetData/s:row/s:c',NS):
                v=c.find('s:v',NS);f=c.find('s:f',NS)
                if f is not None:formulas[c.get('r')]=f.text
                if c.get('t')=='s' and v is not None:value=strings[int(v.text)]
                elif c.get('t')=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//s:t',NS))
                else:value=v.text if v is not None else None
                cells[c.get('r')]=value
            output[sheet.get('name')]={'cells':cells,'formulas':formulas}
        return output
sheets=load_workbook();manual=sheets['人工标注']['cells']
bindings={b['sample_id']:b for b in baseline['bindings']}
orig={r[0]:r for r in baseline['human_rows']}
machines={r[0]:r for r in baseline['model_rows']}
candidates={b['sample_id']:c for b,c in zip(baseline['bindings'],baseline['selected_raw_candidates'])}
fields=[('E','来源支持',{'支持','反驳','证据不足','无法判断'}),
        *[(c,name,{'是','否','不适用','无法判断'}) for c,name in [('F','限定完整'),('G','关系方向正确'),('H','关系映射正确'),('I','本体兼容'),('J','任务相关')]],
        ('K','本体覆盖缺口',{'有','无','无法判断'}),('L','事实表述正确',{'是','否','不适用','无法判断'})]
structural=[];missing=[];invalid=[];annotations=[];counts={name:Counter() for _,name,_ in fields}
seen=set();source_changes=[];all_ids=[]
def flat(value):return str(value or '').replace('\r','').replace('\n','')
for addr,value in manual.items():
    if re.fullmatch(r'A\d+',addr) and str(value or '').startswith('DEV-'):all_ids.append((int(addr[1:]),value))
for row,sid in sorted(all_ids):
    if sid in seen:structural.append({'sample_id':sid,'issue':'duplicate_sample_id'});continue
    seen.add(sid)
    if sid not in bindings:structural.append({'sample_id':sid,'issue':'unknown_sample_id'});continue
    for col,j in [('B',1),('C',2)]:
        if flat(manual.get(f'{col}{row}'))!=flat(orig[sid][j]):source_changes.append({'sample_id':sid,'cell':f'{col}{row}','issue':'frozen_candidate_display_changed'})
    if str(manual.get(f'D{row}') or '').replace('\r\n','\n')!=orig[sid][3].replace('\r\n','\n'):
        source_changes.append({'sample_id':sid,'cell':f'D{row}','issue':'direct_quote_display_changed'})
    labels={};cell_refs={}
    for col,name,allowed in fields:
        addr=f'{col}{row}';value=manual.get(addr);value=value.strip() if isinstance(value,str) else value
        labels[name]=value or None;cell_refs[name]=addr
        if not value:missing.append({'sample_id':sid,'field':name,'cell':addr})
        elif value not in allowed:invalid.append({'sample_id':sid,'field':name,'cell':addr,'value':value})
        if addr in sheets['人工标注']['formulas']:invalid.append({'sample_id':sid,'field':name,'cell':addr,'issue':'formula_in_manual_label'})
        counts[name][value or '未填写']+=1
    annotations.append({**bindings[sid],'source_workbook_row':row,'cell_refs':cell_refs,
        'human_labels_original':labels,'suggested_revision_original':manual.get(f'M{row}'),
        'human_note_original':manual.get(f'N{row}'),'reviewer_original':manual.get(f'O{row}'),
        'review_date_original':manual.get(f'P{row}'),'machine_source_result':machines[sid][3],
        'machine_schema_guard':machines[sid][4],
        'label_source':'user_completed_workbook','label_scope_status':'awaiting_user_clarification',
        'production_admission_authorized':False})
if seen!=set(bindings):structural.append({'issue':'sample_set_changed','missing':sorted(set(bindings)-seen),'extra':sorted(seen-set(bindings))})

# Reconcile the complete exported evidence against its unchanged frozen source.
evidence=sheets['原文证据']['cells'];evidence_changes=[]
for i,r in enumerate(baseline['evidence_rows'],6):
    for col,j in [('A',0),('C',2),('H',7),('I',8)]:
        if str(evidence.get(f'{col}{i}') or '').replace('\r\n','\n')!=str(r[j]).replace('\r\n','\n'):
            evidence_changes.append({'cell':f'{col}{i}','issue':'frozen_evidence_changed'})
    for col,j in [('F',5),('G',6)]:
        if int(float(evidence.get(f'{col}{i}') or -1))!=r[j]:evidence_changes.append({'cell':f'{col}{i}','issue':'offset_changed'})
    assert baseline['source_canonical_text'][r[5]:r[6]]==r[7]
model=sheets['模型对照']['cells'];binding_changes=[]
for i,m in enumerate(baseline['model_rows'],6):
    for col,j in [('A',0),('I',6),('J',7),('K',8),('L',9),('M',10),('N',11),('O',12),('P',13),('Q',14),('R',15)]:
        actual=model.get(f'{col}{i}');expected=m[j]
        if isinstance(expected,int):equal=actual is not None and int(float(actual))==expected
        else:equal=str(actual or '')==str(expected or '')
        if not equal:binding_changes.append({'cell':f'{col}{i}','sample_id':m[0],'issue':'frozen_binding_changed'})
ontology_reference_changes=[]
for i,row in enumerate(baseline['ontology_rows'],6):
    for j,expected in enumerate(row):
        addr=chr(65+j)+str(i);actual=sheets['本体参考']['cells'].get(addr)
        if str(actual or '').replace('\r\n','\n')!=str(expected or '').replace('\r\n','\n'):
            ontology_reference_changes.append({'cell':addr,'issue':'ontology_reference_changed'})
live_version_changes=[]
with sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
    for a in annotations:
        row=c.execute('SELECT r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision WHERE c.candidate_id=?',(a['candidate_id'],)).fetchone()
        if not row or sha256_json(json.loads(row[0]))!=a['candidate_content_hash']:
            live_version_changes.append({'sample_id':a['sample_id'],'issue':'current_candidate_changed_since_freeze'})

issues=[];exhibit_ids=set()
def issue(a,code,description):
    issues.append({'sample_id':a['sample_id'],'workbook_row':a['source_workbook_row'],'code':code,'description':description})
    exhibit_ids.add(a['sample_id'])
for a in annotations:
    h=a['human_labels_original'];sid=a['sample_id'];t=candidates[sid]['triple']
    if h['事实表述正确']=='是' and h['来源支持']!='支持':issue(a,'fact_scope_source','来源支持未标为支持，但事实表述正确为是；需明确来源判断与事实判断的范围。')
    if h['事实表述正确']=='是' and h['限定完整']=='否':issue(a,'fact_scope_qualifiers','限定完整为否、事实表述正确为是；需确认总判断针对原候选还是修订后的表述。')
    if h['事实表述正确']=='是' and h['关系方向正确']=='否':issue(a,'fact_scope_direction','关系方向正确为否、事实表述正确为是，且有修改建议；需确认总判断的对象。')
    if h['事实表述正确']=='否' and h['来源支持']=='支持' and all(h[k]=='是' for k in ('限定完整','关系方向正确','关系映射正确','本体兼容','任务相关')):
        issue(a,'negative_overall_reason','来源与其他分项均为肯定，但事实表述正确为否；需补充判否原因或任务范围口径。')
    direct=orig[sid][3].strip()
    if not any(ch.isalnum() for ch in direct):
        issue(a,'authoring_quote_locator_defect','原主表直接引文仅为公式分隔符，属于样本展示问题。完整核验证据已保留，不据此判人工或模型错误。')
mapping_yes_without_id=[a['sample_id'] for a in annotations if a['human_labels_original']['关系映射正确']=='是' and not candidates[a['sample_id']]['triple'].get('relation_id')]
raw_cross=Counter((a['machine_source_result'],a['human_labels_original']['来源支持']) for a in annotations)
schema_cross=Counter((a['machine_schema_guard'],a['human_labels_original']['本体兼容']) for a in annotations)
strata={}
for group in sorted({a['stratum'] for a in annotations}):
    rows=[a for a in annotations if a['stratum']==group]
    strata[group]={'n':len(rows),'source_support_counts':dict(Counter(a['human_labels_original']['来源支持'] for a in rows)),
                   'fact_correct_counts':dict(Counter(a['human_labels_original']['事实表述正确'] for a in rows))}
seat_cross=defaultdict(Counter)
for a in annotations:
    for line in machines[a['sample_id']][5].split('\n'):
        name,payload=line.split('：',1);vote=json.loads(payload)
        seat_cross[name][(vote.get('source_support'),a['human_labels_original']['来源支持'])]+=1
summary={'checked_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'source_workbook':str(WORKBOOK),'source_workbook_sha256':workbook_hash,'sample_count':len(annotations),
    'core_labels_filled':240-len(missing),'core_labels_expected':240,'missing_core_labels':missing,'invalid_labels':invalid,
    'optional_reviewer_blank_count':sum(not a['reviewer_original'] for a in annotations),
    'optional_review_date_blank_count':sum(not a['review_date_original'] for a in annotations),
    'counts':{k:dict(v) for k,v in counts.items()},'structural_issues':structural,'frozen_candidate_display_changes':source_changes,
    'frozen_evidence_changes':evidence_changes,'frozen_binding_changes':binding_changes,'live_candidate_version_changes':live_version_changes,
    'frozen_ontology_reference_changes':ontology_reference_changes,
    'raw_machine_source_vs_human':[{'machine':m,'human':h,'n':n} for (m,h),n in sorted(raw_cross.items())],
    'raw_machine_guard_vs_human_compatibility':[{'machine':m,'human':h,'n':n} for (m,h),n in sorted(schema_cross.items())],
    'strata':strata,'clarification_issues':issues,'mapping_marked_yes_without_formal_relation_id':mapping_yes_without_id,
    'raw_seat_source_vs_human':{seat:[{'model_source_support':m,'human_source_support':h,'n':n} for (m,h),n in sorted(values.items())] for seat,values in seat_cross.items()},
    'annotation_scope_confirmed':False,'formal_evaluation_metrics_finalized':False,'production_database_written':False,'source_workbook_modified':False,
    'model_calls':0,'sampling_is_population_representative':False}
assert hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()==workbook_hash,'workbook changed during read'
(OUT/'annotations_original.json').write_text(json.dumps({'source_workbook_sha256':workbook_hash,'frozen_manifest_hash':baseline['manifest']['manifest_hash'],'annotations':annotations},ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'annotation_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

lines=['# 30条开发样本人工标注核对（2026-11-03）','',
    f"已读取 {len(annotations)}/30 条样本，核心判断填写 {summary['core_labels_filled']}/240 项，非法选项 {len(invalid)} 项。姓名、日期属于可选信息，空白不计未完成。",'',
    '## 冻结内容核验','',
    f"样本集合/重复问题 {len(structural)} 项；候选显示内容变化 {len(source_changes)} 项；原文证据/坐标变化 {len(evidence_changes)} 项；绑定信息变化 {len(binding_changes)} 项；本体参考内容变化 {len(ontology_reference_changes)} 项；当前库候选版本变化 {len(live_version_changes)} 项。",'',
    '原填写文件仅作只读检查，全部原始标注保留。本次未写回候选库、未准入、未发布、未训练校准器、未调用模型。','',
    '## 原始标注分布','',
    '| 维度 | 填写分布 |','|---|---|']
for name,counter in counts.items():lines.append('| '+name+' | '+'；'.join(k+' '+str(v)+'条' for k,v in counter.items())+' |')
lines+=['','## 当前来源共识与人工来源判断的原始对照','',
    '此表按已填单元格原样计数，等待口径确认后再决定可用于哪些指标。机器的 unresolved 表示未形成共识，不是拒绝。','',
    '| 当前机器来源结果 | 人工支持 | 人工反驳 | 人工证据不足 | 人工无法判断 |','|---|---:|---:|---:|---:|']
for machine in ('supported','unresolved'):
    lines.append('| '+machine+' | '+' | '.join(str(raw_cross[(machine,h)]) for h in ('支持','反驳','证据不足','无法判断'))+' |')
lines+=['','各审核席位的来源字段与程序汇总的来源共识分别记录。程序未形成共识不代表模型已判反驳；正式分析须区分语义意见、检查依据完整性和执行完整性。','',
    '| 审核席位 | 模型来源字段 | 人工来源字段 | 条数 |','|---|---|---|---:|']
for seat,values in seat_cross.items():
    for (machine,human),count in sorted(values.items()):lines.append('| '+seat+' | '+str(machine)+' | '+str(human)+' | '+str(count)+' |')
lines+=['','## 需要确认的口径与展示问题','',
    '| 样本 | 项目 | 说明 |','|---|---|---|']
for x in issues:lines.append('| '+x['sample_id']+' | '+x['code']+' | '+x['description']+' |')
lines+=['',
    '“事实表述正确”需要明确针对当前原候选、建议修订后的候选，还是是否适合入图。三种口径不能混作同一真值。',
    f"另有 {len(mapping_yes_without_id)} 条未指定正式关系ID却被标为关系映射正确："+'、'.join(mapping_yes_without_id)+'。这可能是在评价表面关系语义；需与正式ID映射口径区分，暂不按该列计算映射准确率。',
    '30条均填本体兼容为是、覆盖缺口为无。概念存在、当前候选ID完整以及约束兼容是不同问题，需明确标注含义；机器检查未知不自动解释为人工标注错误。',
    '如果确认本批没有本体缺口正例，该维度召回率及动态扩展的缺口保留收益无法由本批计算。后续样本应独立核验，不改标签来配合实验设计。','',
    '、'.join(x['sample_id'] for x in issues if x['code']=='authoring_quote_locator_defect')+' 的主表引文仅含公式分隔符，是前次导出选取最近定位片段的疏漏；原文证据表中的完整正文一直保留。本次另附完整证据供复核，不覆盖现有标签，也不直接作废任何标签。','',
    '## 下一步','',
    '先确认上述口径和个别判否依据，再冻结确认版人工标签。之后进行同一候选池、相同预算的开发对照和消融实验；这30条属于定向分层开发集，不能外推为全章准确率或作为独立测试集。','',
    '来源文件：'+str(WORKBOOK),
    '来源文件 SHA-256：'+workbook_hash,'']
(OUT/'人工标注核对报告.md').write_text('\n'.join(lines),encoding='utf-8')

supplement=['# 待确认样本及补充原文','',
    '原始人工判断保持不变。以下原文来自已冻结的核验证据；这些材料不预先指定支持或反驳角色。','']
for sid in sorted(exhibit_ids):
    a=next(a for a in annotations if a['sample_id']==sid);c=candidates[sid];t=c['triple']
    supplement+=['## '+sid,'',
        '候选：'+t['head']+' → '+t['surface_relation']+' → '+str(t['tail']),
        '当前限定：'+json.dumps(t.get('qualifiers'),ensure_ascii=False),
        '原人工判断：'+json.dumps(a['human_labels_original'],ensure_ascii=False),
        '原修改建议：'+str(a.get('suggested_revision_original') or ''),
        '原备注：'+str(a.get('human_note_original') or ''),'']
    for row in baseline['evidence_rows']:
        if row[0]!=sid:continue
        supplement+=['### 证据 '+str(row[1])+'，片段 '+row[3],
            '章节：'+str(row[4])+'；全文字符位置 ['+str(row[5])+', '+str(row[6])+')；证据ID '+row[2],'',
            '```text',row[7],'```','',
            '相邻上下文：','```text',row[8],'```','']
(OUT/'待确认样本与完整原文.md').write_text('\n'.join(supplement),encoding='utf-8')
print(json.dumps({k:summary[k] for k in ('sample_count','core_labels_filled','counts','raw_machine_source_vs_human','raw_machine_guard_vs_human_compatibility','frozen_candidate_display_changes','frozen_evidence_changes','frozen_binding_changes','live_candidate_version_changes')},ensure_ascii=False,indent=2))
print(json.dumps({'clarification_issues':issues,'mapping_yes_without_id':mapping_yes_without_id},ensure_ascii=False,indent=2))
