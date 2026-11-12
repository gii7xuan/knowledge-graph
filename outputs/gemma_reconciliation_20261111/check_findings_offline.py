"""Triage actual objections against frozen source text; no votes are edited."""
import difflib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manual_gemma_reassessment import HERE, OUT, read, write, now
from ontology_review.delta import catalog


def main():
    proposal = read(OUT / 'prepared_proposal.json')
    request = read(OUT / 'prepared_competency_request.json')
    result = read(HERE / 'whole_chapter_result.json')
    document = request['source_documents'][0]
    text = document['text']
    votes = {v['voter_id']: v for v in result['votes']}
    items = catalog(proposal['merged_schema'])
    deepseek = votes['judge_c']
    assert deepseek['response_status'] == 'ok'
    rows = []
    for term in deepseek['unresolved_terms']:
        position = text.find(term)
        matched = [item for item in items.values() if item.get('label') == term]
        start, end = max(0, position - 70), min(len(text), position + len(term) + 150)
        rows.append({'term': term, 'schema_ids': [item['id'] for item in matched],
            'schema_kinds': sorted({item['schema_kind'] for item in matched}),
            'literal_source_occurrences': text.count(term),
            'first_source_excerpt': ({'document_id': document['document_id'],
                'document_version': document['document_version'], 'source_index_hash': document['source_index_hash'],
                'offset_basis': 'canonical_document', 'start': start, 'end': end, 'quote': text[start:end]}
                if position >= 0 else None),
            'semantic_support_confirmed_by_literal_match': False})
    gemma = votes['judge_b']
    body = gemma['raw_response']
    if isinstance(body, str):
        body = json.loads(body)
    quotations = []
    for assessment in body['assessments']:
        for index, quote in enumerate(assessment['quotes']):
            quotations.append({'index': index, **quote, 'exact_occurrences': text.count(quote['quote'])})
    bad = quotations[0]['quote']
    beginning = text.index('最后再根据状态方程')
    ending = text.index('\n', beginning)
    candidate = text[beginning:ending]
    assert bad.replace('\\mathrm {s}', '\\mathrm{s}') == candidate
    assert text.count(candidate) == 1
    diagnosis = {'original_response_status': gemma['response_status'],
        'all_quote_checks': quotations, 'invalid_quote_index': 0,
        'raw_model_quote': bad, 'exact_frozen_source_candidate': candidate,
        'candidate_start': beginning, 'candidate_end': ending,
        'cause': 'Extra space in LaTeX command: \\mathrm {s} differs from source \\mathrm{s}',
        'character_diff': [change for change in difflib.ndiff(bad, candidate) if change[0] != ' '],
        'vote_repaired_or_promoted': False, 'new_model_calls': 0}
    literal_hits = sum(row['literal_source_occurrences'] > 0 for row in rows)
    literal_misses = len(rows) - literal_hits
    scope = {'qwen_contract_valid': True, 'qwen_model_claimed_coverage': votes['judge_a']['coverage'],
        'qwen_note': votes['judge_a']['assessments'][0]['note'],
        'offline_ai_scope_observation': 'The explanation and quotations concentrate on the eight changed relations; they do not provide an item-by-item baseline coverage demonstration. Keep the original pass unchanged, but do not treat the stated complete flag as independent proof of exhaustive coverage.',
        'deepseek_contract_valid': True, 'deepseek_original_fail_preserved': True,
        'deepseek_unresolved_terms': len(rows), 'literal_hits_in_frozen_chapter': literal_hits,
        'literal_misses_in_frozen_chapter': literal_misses,
        'interpretation': 'Literal hits refute an unqualified absence claim only. Presence alone does not verify a class definition or relation meaning, and absence alone does not establish lack of semantic grounding.',
        'source_ambiguity_example': {
            'term': '亚同温层',
            'passages': [
                '中间层（又称亚同温层）',
                '对流层与平流层之间的过渡层称为亚同温层。',
            ],
            'observation': 'Both passages occur in the frozen chapter. Preserve both and investigate source terminology; do not silently rewrite the ontology from only one passage.',
        }, 'model_votes_edited': False, 'network_calls': 0, 'at': now()}
    assert all(fragment in text for fragment in scope['source_ambiguity_example']['passages'])
    write(HERE / 'deepseek_objection_source_crosswalk.json', {'terms': rows, 'interpretation': scope['interpretation']})
    write(HERE / 'gemma_quote_diagnosis.json', diagnosis)
    write(HERE / 'coverage_scope_and_objection_checks.json', scope)
    section = ('\n## 离线复核与下一步问题\n\n'
        '三席调用已执行，但全章覆盖审核未达到有效3/3门槛。\n\n'
        '- Gemma的8个引用中，7个精确匹配；第1个把源文LaTeX `\\mathrm{s}` 写成 `\\mathrm {s}`，多了一个空格。原响应仍为invalid_contract，没有程序改票或追加模型纠正。详见gemma_quote_diagnosis.json。\n'
        f'- DeepSeek列出130个未决术语；其中{literal_hits}个可在冻结全文逐字找到，{literal_misses}个未逐字找到。已逐项导出ID、次数、原文位置及上下文。文字出现不等于定义和关系已被证明；130项也不能直接当作130个已确认错误。原fail票保留。\n'
        '- Qwen虽然声明complete/pass，其说明和引用主要围绕8项改动，尚未展示完整基线逐项覆盖。已保存该范围问题，不把JSON契约通过等同于全章语义已证明。\n'
        '- 冻结章节对“亚同温层”同时出现“中间层（又称亚同温层）”和“对流层与平流层之间的过渡层称为亚同温层”。应同时保留这两处材料，先核对来源与术语，再决定定义修订。\n\n'
        '本轮9次额度已用完。下一轮应围绕这些具体问题准备原席位的定向澄清或真实定义修订，再办理额外调用授权；本轮没有缩小原问题、复投有效fail或发布版本。\n')
    for path in (HERE / 'report.md', OUT / 'report.md'):
        original = path.read_text(encoding='utf-8')
        if '## 离线复核与下一步问题' not in original:
            path.write_text(original + section, encoding='utf-8')
    print(json.dumps({'deepseek_unresolved_terms': len(rows), 'literal_hits': literal_hits,
                      'literal_misses': literal_misses, 'gemma_bad_quotes': 1,
                      'votes_edited': False, 'network_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
