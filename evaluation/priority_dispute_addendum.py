"""Offline visual-source addendum. Never dispatch requests or apply revisions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from .freeze_shared_ontology import ROOT, read, write
from review_agent.contracts import sha256_json
from review_agent.revision_patch import merge_triple_patch, expression_diagnostics


OUT = ROOT / 'outputs/priority_disputes_20261118'
POOL = ROOT / 'outputs/development_pool_20261117'
FOLLOWUP = ROOT / 'outputs/preannotation_followup_20261118'
BOOKS = Path('D:/长理研究生/火炮方面')
IDS = ('PILOT-001', 'PILOT-002', 'PILOT-011', 'PILOT-017', 'PILOT-020', 'PILOT-027', 'PILOT-036')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def proof(path, pdf_page, printed_page, images, finding):
    return {'path': str(path), 'sha256': digest(path), 'pdf_page_one_based': pdf_page,
            'printed_page': printed_page, 'inspection_method': 'coding_agent_visual_inspection_of_poppler_render',
            'images': [{'path': str(OUT / image), 'sha256': digest(OUT / image)} for image in images],
            'finding': finding, 'counts_as_human_review': False, 'registered_text_bundle_evidence': False}


def build():
    samples = {r['sample_id']: r for r in read(POOL / 'pilot/review_samples.json')['samples']}
    prepared = {r['sample_id']: r for r in read(OUT / 'prepared_cases.json')}
    report = read(OUT / 'report.json')
    snapshots = {str(p): digest(p) for p in (OUT / 'review.sqlite3', OUT / 'report.json', OUT / 'dispositions.json',
        OUT / 'protocol.json', FOLLOWUP / 'priority_answers_blank.json', POOL / 'pilot/annotations_A_blank.json') if p.exists()}
    visual = {
        'PILOT-011': proof(BOOKS / '火炮设计理论 (张相炎、郑建国、袁人枢) (Z-Library).pdf', 108, 96,
            ['design_page108.png', 'operator_original_detail.png'],
            'In section 3.2.2 连续方程, k is followed by a baseline Chinese full stop 。, not a mathematical circular subscript. The Markdown k_{circ} attaches sentence punctuation to the symbol.'),
        'PILOT-036': proof(BOOKS / 'Ballistics  Theory and Design of Guns and Ammunition, Third Edition.pdf', 41, 22,
            ['third_edition_page41.png'],
            'The matching thermodynamics paragraph prints δ followed by two elevated strokes (double-prime/quote-like glyph). Preserve the original δ\'\' transcription; this check does not establish the semantic role or exact Unicode identity of the strokes.'),
    }
    for sid, item in visual.items():
        item['original_binding'] = samples[sid]['binding']
        item['original_markdown_anchors'] = samples[sid]['original_source_anchors']
    visual['user_pdf_page75'] = proof(BOOKS / '火炮弹道学 (钱林方) (Z-Library).pdf', 75, None,
        ['user_pdf_page75.png'],
        'This different book has section 3.2.2 弹丸绕质心运动方程组 here. It is not the source of PILOT-011; no subject-matter equations from that page were used to resolve the candidate.')

    # New proposals are data only. The original attempted draft and all receipts remain immutable.
    one = deepcopy(samples['PILOT-001']['candidate']['triple']['semantic_context'])
    one['knowledge_scope'] = 'local_definition'
    one['relation_meaning'] = {'semantic_equivalence': 'needs_review', 'note':
        "Preserve covolume / does not have / physical meaning and negative polarity. Read the claim within the author's 'Think of it as' interpretive instruction. The preceding 'has been described as' is an attributed description, not automatically a direct contradiction. Do not treat this local instruction as universal denial of all physical interpretations."}
    thirty_six = deepcopy(samples['PILOT-036']['candidate']['triple']['semantic_context'])
    thirty_six['knowledge_scope'] = 'local_definition'
    thirty_six['relation_meaning'] = {'semantic_equivalence': 'needs_review', 'note':
        "A conditional normative requirement in the discussion of Equation 2.43, with lexical 'would' preserved in the relation and modality attachment. The authors explicitly ignore this convention for their purposes. Enum unknown denotes unencoded modality, not missing source wording or asserted physical truth."}
    thirty_six['qualifier_attachments'].append({'qualifier_kind': 'discourse', 'attachment': 'relation',
        'value': 'but for our purposes, we shall ignore this fact',
        'scope_id': samples['PILOT-036']['candidate']['section_id']})
    patches = {
        'PILOT-001': {'semantic_context': one},
        'PILOT-011': {'value': samples['PILOT-011']['candidate']['triple']['tail'],
            'datatype': 'http://www.w3.org/2001/XMLSchema#string'},
        'PILOT-036': {'qualifiers': {'modality': 'unknown'}, 'semantic_context': thirty_six},
    }
    plans = []
    for sid, patch in patches.items():
        before = samples[sid]['candidate']['triple']
        after = merge_triple_patch(before, patch, strict=True)
        for key in ('head', 'surface_relation', 'tail', 'tail_kind', 'relation_id', 'mapping_status'):
            assert after[key] == before[key], (sid, key)
        assert after['qualifiers']['polarity'] == before['qualifiers']['polarity']
        assert after['qualifier_status'] == before['qualifier_status']
        if sid != 'PILOT-036':
            assert after['qualifiers'] == before['qualifiers']
        plans.append({'plan_id': 'offline_pdf_addendum_v1:' + sid, 'sample_id': sid,
            'status': 'offline_proposal_pending_independent_review', 'original_binding': samples[sid]['binding'],
            'patch': patch, 'before_triple_hash': sha256_json(before), 'after_triple_hash': sha256_json(after),
            'after_triple': after, 'structural_patch_validation_passed': True,
            'expression_diagnostics': expression_diagnostics({'contract_version': 'extraction_candidate_v4', 'triple': after}),
            'pdf_visual_proof_required': sid in visual, 'new_evidence_registration_required': sid in visual,
            'visual_proof_ref': 'source_visual_addendum.json#' + sid if sid in visual else None,
            'automatically_executable': False, 'applied': False, 'counts_as_human_gold': False})

    findings = {
        'PILOT-001': ('original_meta_rewrite_superseded_in_work_plan',
            '保留 covolume 主体、原否定关系和尾项，仅在语义上下文限定 Think of it as 的作者解释口吻。前句是被引述的解释，与后句不自动构成事实矛盾。首版 this passage 改写不继续采用；原拒绝意见保留，新的最小方案仍待独立复核。'),
        'PILOT-002': ('semantic_draft_prepared_not_reviewed',
            'thermodynamics 不是 introduced 的施事。草案改以 work and energy 为主体，记录其在本书前文作为主要热力学概念被介绍；earlier sections 为篇章位置，从 time_scope 移出。'),
        'PILOT-011': ('ocr_source_question_resolved_representation_proposed',
            '原页 k 后为句号，解除 k 圆下标疑问。原候选已有普通 k，保持尾项；为 literal 补 value=原尾项、datatype=xsd:string 的待审方案。字符串类型仅用于保存转写，不代表数学结构或向量粗体已经完整建模。'),
        'PILOT-017': ('semantic_draft_prepared_not_reviewed',
            '选择控制体的是文中分析者 we，Eulerian frame 是分析场景。草案改为文中欧拉参考系分析使用选定的真实或假想控制体，保留局部范围。'),
        'PILOT-020': ('local_equation_identity_resolved_draft_not_reviewed',
            '同一原书连续段落将右端第二项绑定到式 2.129。草案保留原文 material 的局部解释，不把它泛化为任意公式第二项，也不额外断言累计质量或新增物理量解释。'),
        'PILOT-027': ('semantic_draft_prepared_not_reviewed',
            '有势质量力为本段理想流体欧拉方程沿流线积分推导的前提之一。草案补全定常且（正压或不可压缩）的其余条件，保留 AND/OR 范围，禁止改成所有质量力的普遍性质。'),
        'PILOT-036': ('notation_preserved_modality_encoding_proposed_still_unknown',
            '原书确有 δ 后双划记号，保留 δ\'\'；would require 为规范性语气，作者随后明确忽略该惯例。草案仅将无法编码的 modality 值设为 unknown，并保留关系及词汇附着中的 would。未知状态继续阻止完整表达准入，不能当作 asserted。'),
    }
    rows = []
    for sid in IDS:
        status, finding = findings[sid]
        row = {'sample_id': sid, 'original_binding': samples[sid]['binding'], 'status': status,
            'finding': finding, 'analysis_origin': 'coding_agent_source_analysis',
            'human_adjudicated': False, 'admitted': False, 'mapping_status': 'unassigned',
            'source_supplements_ref': str(FOLLOWUP / 'source_supplements.json') + '#' + sid,
            'offline_plan_id': 'offline_pdf_addendum_v1:' + sid if sid in patches else None,
            'initial_draft_id': prepared[sid]['draft']['draft_id'] if sid in prepared else None,
            'independent_review_status': 'review_incomplete' if sid == 'PILOT-001' else 'not_run'}
        if sid == 'PILOT-001':
            row.update(initial_review_ref=str(OUT / 'PILOT-001_review.json'),
                rejection_attempt_id='attempt_29fc01a3f13f4dffbf5c495efb3d05f5',
                raw_rejection_retained=True, raw_rejection_is_usable_vote=False)
        rows.append(row)

    with sqlite3.connect((OUT / 'review.sqlite3').as_uri() + '?mode=ro', uri=True) as con:
        con.row_factory = sqlite3.Row
        receipts = [dict(row) for row in con.execute('SELECT attempt_id,state,result_json FROM request_attempts')]
        costs = [json.loads(row['result_json'] or '{}') for row in receipts]
        counts = {table: con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
            for table in ('jobs', 'import_outbox', 'graph_outbox', 'ontology_publish_outbox')}
        drafts = dict(con.execute('SELECT status,COUNT(*) FROM revision_drafts GROUP BY status'))
    watched = {**read(FOLLOWUP / 'baseline.json')['watched_hashes'], **read(OUT / 'baseline.json')['watched_hashes'], **snapshots}
    unchanged = {path: digest(path) == h for path, h in watched.items()}
    verified = {'all_original_inputs_and_execution_artifacts_unchanged': all(unchanged.values()),
        'hash_checks': unchanged, 'request_attempts': len(receipts),
        'unknown_attempt_ids': [r['attempt_id'] for r in receipts if r['state'] == 'unknown'],
        'known_input_tokens': sum(c.get('input_tokens') or 0 for c in costs),
        'known_output_tokens': sum(c.get('output_tokens') or 0 for c in costs),
        'unknown_usage_is_zero': False, 'price': None, 'new_calls_for_addendum': 0,
        'outbox_and_job_counts': counts, 'initial_draft_status_counts': drafts,
        'offline_plans': len(plans), 'applied_revisions': 0, 'human_gold_count': 0,
        'model_reliability_updated': False, 'paper_accuracy': None, 'production_written': False,
        'source_question_resolved_is_not_revision_approval': True}
    assert all(unchanged.values()) and len(receipts) == report['calls'] == 2
    assert len(verified['unknown_attempt_ids']) == 1 and all(v == 0 for v in counts.values())
    assert not ({'approved', 'applied'} & set(drafts))
    write(OUT / 'source_visual_addendum.json', visual)
    write(OUT / 'offline_revision_plans.json', plans)
    write(OUT / 'seven_dispute_resolutions.json', {'version': 'priority_disputes_addendum_v1', 'rows': rows,
        'supersedes_initial_dispositions_for_current_work_plan': True,
        'initial_dispositions_and_execution_report_preserved': True})
    write(OUT / 'completion_verification.json', verified)
    print(json.dumps({k: v for k, v in verified.items() if k != 'hash_checks'}, ensure_ascii=False))


if __name__ == '__main__':
    build()
