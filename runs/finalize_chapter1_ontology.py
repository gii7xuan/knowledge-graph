"""Add exact original-book coordinates and a readable draft handoff."""
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
RUN=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'runs/ontology/20261027_173531_405f6f75'
INPUT=ROOT/'runs/ontology_chapter1_inputs'


def write(path,data): path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    report=json.loads((RUN/'chapter1_generation_report.json').read_text(encoding='utf-8'))
    origin=json.loads((INPUT/'source_manifest.json').read_text(encoding='utf-8'))
    full=json.loads((INPUT/'full_source_index.json').read_text(encoding='utf-8'))
    index=json.loads((RUN/'document_index.json').read_text(encoding='utf-8'))
    chapter=index['source_snapshot']['canonical_text'];text=full['source_snapshot']['canonical_text']
    if text[origin['chapter_start']:origin['chapter_end']]!=chapter: raise ValueError('chapter-to-book coordinate mapping differs')
    chunks={c['chunk_id']:c for c in index['chunks']}
    provenance=json.loads((RUN/'chapter1_provenance.json').read_text(encoding='utf-8'))
    data=json.loads((RUN/'normalized_candidates.json').read_text(encoding='utf-8'))
    by_id={item['candidate_id']:item for section in ('candidate_classes','candidate_object_properties')
        for item in data.get(section,[])}
    located=0;unknown=0
    for row in provenance:
        item=by_id.get(row.get('candidate_id'),{})
        evidence_rows=list(row.get('evidence',[]))
        for duplicate in item.get('merged_duplicates',[]):
            for evidence in duplicate.get('evidence',[]):
                if evidence not in evidence_rows: evidence_rows.append(evidence)
        row['evidence']=evidence_rows
        anchors=[]
        for evidence in row.get('evidence',[]):
            quote=evidence.get('text') or evidence.get('quote') or ''
            chunk=chunks.get(evidence.get('chunk_id'))
            start,end=(chunk['document_start'],chunk['document_end']) if chunk else (0,len(chapter))
            matches=[m.start()+start for m in re.finditer(re.escape(quote),chapter[start:end])] if quote else []
            if len(matches)==1:
                a=origin['chapter_start']+matches[0];b=a+len(quote)
                if text[a:b]!=quote: raise ValueError('source anchor slice mismatch')
                anchors.append({'document_id':full['document_id'],'document_version':full['document_version'],
                    'source_snapshot_id':full['source_snapshot']['snapshot_id'],'start':a,'end':b,
                    'offset_unit':'unicode_code_point','span_convention':'[start,end)','quote':quote,'anchor_valid':True})
                located+=1
            else:
                anchors.append({'quote':quote,'anchor_valid':False,'reason':'ambiguous_or_unlocated','candidate_matches':len(matches)})
                unknown+=1
        row['book_source_anchors']=anchors
    write(RUN/'chapter1_provenance.json',provenance)
    pending=json.loads((RUN/'chapter1_pending_modeling.json').read_text(encoding='utf-8'))
    ids={x.get('candidate_id') for x in pending}
    for section in ('candidate_constraints','uncertain_candidates','ontology_mapping'):
        for item in data.get(section,[]):
            if item.get('candidate_id') not in ids:
                pending.append({'candidate_id':item.get('candidate_id'),'section':section,'reason':'separate_modeling_review_required','candidate':item})
                ids.add(item.get('candidate_id'))
    write(RUN/'chapter1_pending_modeling.json',pending)
    report.update(exact_book_source_anchors=located,ambiguous_or_unlocated_source_anchors=unknown,pending_modeling_count=len(pending))
    journal=[json.loads(line) for path in sorted(INPUT.glob('*model_requests.jsonl'))
        for line in path.read_text(encoding='utf-8').splitlines()]
    complete=[r for r in journal if r['status']=='completed']
    report['model_usage']={'completed_calls':len(complete),'failed_calls':sum(r['status']=='failed' for r in journal),
        'known_input_tokens':sum(r.get('input_tokens') or 0 for r in journal if r['status']!='started'),
        'known_output_tokens':sum(r.get('output_tokens') or 0 for r in journal if r['status']!='started'),
        'unknown_usage_requests':sum(r.get('input_tokens') is None or r.get('output_tokens') is None for r in journal if r['status']!='started'),
        'returned_model_ids':sorted({r['model_returned'] for r in complete if r.get('model_returned')})}
    status_rows=json.loads((RUN/'raw_responses/chunk_status.json').read_text(encoding='utf-8'))
    report['failed_chunk_ids']=[r['chunk_id'] for r in status_rows if r['status']=='failed']
    report['coverage_note']='Chunk execution coverage does not establish semantic completeness.'
    report['source_role']='build; not an independent held-out test corpus'
    write(RUN/'chapter1_generation_report.json',report)
    from ontologyBuild.release_catalog import ReleaseCatalog
    if ReleaseCatalog(ROOT/'runs/ontology_releases').current() is not None: raise ValueError('generation unexpectedly activated a release')
    preview=json.loads((RUN/'chapter1_import_preview.json').read_text(encoding='utf-8'))
    classes=json.loads((Path(report['draft_path'])/'ontology_snapshot.json').read_text(encoding='utf-8'))['classes']
    lines=['# 《火炮弹道学》第一章概念本体草案','',
        '来源：钱林方《火炮弹道学（第2版）》第1章《质点弹道基本方程及其解法》。目录与第二章未进入本次构建。',
        f"正文规范字符区间 [{origin['chapter_start']}, {origin['chapter_end']})，共 {origin['chapter_characters']} 个字符；所有源偏移为 Unicode 码点。",'',
        f"生成 {report['class_count']} 个类、{report['object_property_count']} 个对象属性、{report['subclass_edge_count']} 条候选父类关系。",'',
        '采用当前项目的来源约束 LLM 候选生成流程，实际模型：'+', '.join(report['model_usage']['returned_model_ids'])+'。作者 NeOn 脚本未执行。',
        '本次范围为教学概念、定义、分类和描述性关系；不包含公式、数值数据属性或操作/修正/优化规则。',
        f"片段执行：{report['extraction_stats']['successful_chunks']}/{report['extraction_stats']['total_chunks']} 成功，失败 {report['extraction_stats']['failed_chunks']}；合法空结果另行记录。",'',
        '生成方式分布：'+json.dumps(report.get('source_generation_methods',{}),ensure_ascii=False)+'。',
        '助手补录部分仅保留原文明示名称，未推断定义、父类或关系；不计入 Qwen 请求成功数。','',
        f"整书证据精确定位 {located} 处，未定位或有歧义 {unknown} 处。片段执行覆盖率不代表语义建模完整率；本章属于构建语料。",'',
        f"项目结构校验：{report['structure_valid']}；独立语义审核待进行，推理器未运行，当前未发布。",'',
        '- [Turtle 草案](chapter1_ontology_draft.ttl)',
        '- [生成及覆盖报告](chapter1_generation_report.json)',
        '- [逐项候选](normalized_candidates.json)',
        '- [整书来源锚点](chapter1_provenance.json)',
        '- [待建模项目](chapter1_pending_modeling.json)',
        f"- 已冻结的导入预览：`{report['draft_path']}`；artifact_digest：`{preview['artifact_digest']}`。",'',
        '结构通过仅表示当前受限视图可解析，不表示定义、父类和 domain/range 已被领域审核批准。未定位或多位置引文继续保留待核对，不猜测偏移。', '',
        '## 类别清单','']
    for item in sorted(classes.values(),key=lambda x:x['label']):
        lines.append('- '+item['label'])
    (RUN/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('class_count','object_property_count','subclass_edge_count','structure_valid','exact_book_source_anchors','ambiguous_or_unlocated_source_anchors','published')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
