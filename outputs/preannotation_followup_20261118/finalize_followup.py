"""Prepare human questions and verify an unmodified source cohort; no model calls."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evaluation.freeze_shared_ontology import read,write
from evaluation.review_samples import ReadOnlyRepository
from extract_triplets.qualifier_wire import validate
from review_agent.contracts import sha256_json
from document.snapshot import read_snapshot
from review_agent.annotation_contract import training_record,DIMENSIONS

OUT=Path(__file__).resolve().parent
POOL=ROOT/'outputs/development_pool_20261117'
baseline=read(OUT/'baseline.json');pre=read(OUT/'ai_preannotations.json');summary=read(OUT/'summary.json')
samples={s['sample_id']:s for s in read(POOL/'pilot/review_samples.json')['samples']}
questions={q['sample_id']:q for q in read(OUT/'review_queue.json')}
supplements=read(OUT/'source_supplements.json')
plans=read(OUT/'wire_repair_plans.json');drafts=read(OUT/'revision_drafts.json')
by_candidate={s['candidate']['candidate_id']:s['sample_id'] for s in samples.values()}

QUESTIONS={
 'PILOT-001':'作者的 Think of it as 限定能否省略？同段对参数物理解释的张力，应怎样分别记录引述、局部解释和原命题支持？',
 'PILOT-002':'原文介绍功和能的施事是否为教材前文，而不是 thermodynamics 这门学科？earlier sections 应放在什么范围字段？',
 'PILOT-011':'请对照原页确认 k_{circ} 的附记是否具有符号意义。仅有当前 Markdown 时不能用常见公式替换。',
 'PILOT-017':'原文在 Eulerian frame 中选择控制体的施事是谁？参考系能否在该句中直接作 chooses 的主体？',
 'PILOT-020':'已补回同一原文段落及式 2.129。请确认右端第二项的身份、原文所指物理量和该候选应保留的局部范围。',
 'PILOT-027':'有势质量力是何推导的前提？原候选残缺的如果流动满足是否足以约束主句？',
 'PILOT-036':'请对照原页确认 δ 双撇的记号；如何保留 would require 的规范性语气及作者随后忽略该规范的说明？'
}
cards=[];blank=[]
for sid,question in QUESTIONS.items():
    sample=samples[sid];q=questions[sid];c=sample['candidate'];t=c['triple']
    cards.append(f'## {sid}\n\n原候选：{t["head"]} → {t["surface_relation"]} → {t["tail"]}\n\n核查问题：{question}\n\n'
        +'AI 预标（待复核）：'+json.dumps(q['ai_labels'],ensure_ascii=False)+'\n\nAI 理由：'+str(q['ai_note'])+'\n\n原始引用：\n\n'
        +'\n\n'.join('> '+e['quote'].replace('\n','\n> ') for e in c['evidence_items'])
        +'\n\n补充原文及精确位置：\n\n'
        +'\n\n'.join('['+ctx['section']+'] '+item['evidence_id']+f'，原书字符 [{item["start"]},{item["end"]})\n\n'+item['quote']
            for ctx in supplements[sid]['contexts'] for item in ctx['items'])
        +'\n\n人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。\n')
    blank.append({'sample_id':sid,'candidate_binding':sample['binding'],'supplement_hash':sha256_json(supplements[sid]),
        'question':question,'answer':None,'reviewer':None,'annotated_at':None,'duration_seconds':None,
        'saw_ai_preannotation':True,'original_evidence_ids':[],'supplemental_evidence_ids':[],
        'adjudication_status':'pending','automatic_gold_created':False})
(OUT/'priority_review_questions.md').write_text('# 首批七项争议核查\n\n本单展示了 AI 预标及补充材料，属于辅助复核材料，不能声称为盲标结果。保留原始命题及其绑定，不将 AI 的修改建议作为原命题真值。\n\n'+'\n\n'.join(cards),encoding='utf-8')
write(OUT/'priority_answers_blank.json',{'scope':'assisted_followup_not_blind_gold','answers':blank})
resolutions=[{'sample_id':'PILOT-004','material_gap':'式3.9的完整局部来源','material_available':True,
    'source_packet_hash':sha256_json(supplements['PILOT-004']),'candidate_or_original_label_changed':False},
 {'sample_id':'PILOT-020','material_gap':'右端第二项所属公式与连续原文','material_available':True,'formula_tag_in_source':'2.129',
    'source_packet_hash':sha256_json(supplements['PILOT-020']),'candidate_or_original_label_changed':False},
 *[{'sample_id':sid,'material_gap':'引起关系的正式域和值域类释义','material_available':True,
    'formal_relation_id':'R_5d7591372f1859dc86e0','domain':'横风','range':'侧偏',
    'this_relation_reused_for_molecular_pressure_claim':False,'all_ontology_coverage_resolved':False} for sid in ('PILOT-015','PILOT-034')]]
write(OUT/'material_gap_progress.json',resolutions)
coverage=[]
for q in questions.values():
    sample=samples[q['sample_id']]
    coverage.append({'sample_id':q['sample_id'],'binding':sample['binding'],'original_expression':sample['candidate']['triple'],
        'ai_coverage_claim':q['ai_labels']['schema_coverage'],'label_status':'pending_independent_ontology_review',
        'work_required':['check exact definition and domain/range, not only relation name',
            'distinguish reuse, missing definition/type, and genuinely missing property',
            'if a shared addition is needed, draft definitions and source proofs for all comparison methods'],
        'proposed_formal_relation_id':None,'proposed_domain':None,'proposed_range':None,
        'source_evidence_ids':[e['evidence_id'] for e in sample['evidence_bundle']['items']],
        'ontology_published':False})
write(OUT/'ontology_coverage_worklist.json',coverage)

assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline['watched_hashes'].items())
assert len(pre['annotations'])==40 and pre['human_gold_count']==0
for row in pre['annotations']:
    assert row['label_source']=='ai_preannotation' and not row['counts_as_human_gold']
    assert not row['eligible_for_reliability_training'] and not row['eligible_for_paper_scoring']
    assert row['adjudication']['adjudication_status']=='pending'
    assert row['adjudication']['adjudicated_label']=={}
    assert all(training_record({'adjudication_status':'pending'},dimension) is None for dimension in DIMENSIONS)
for plan in plans:
    assert validate(plan['original_raw'],plan['projected_raw'],plan['proof'])==[]
    draft=next(d for d in drafts if d['candidate_id']==plan['candidate_id'])
    assert draft['changes']==plan['changes'] and draft['status']=='proposed'
    assert draft['requires_independent_revision_review']
    for field in draft['before']['triple']:
        if field not in {'qualifiers','qualifier_status','mapping_status'}:
            assert draft['before']['triple'][field]==draft['after']['triple'][field]
    assert not draft['after']['triple']['relation_id']
    assert draft['before']['evidence_items']==draft['after']['evidence_items']
for s in supplements.values():
    assert s['new_round_required_for_use'] and not s['original_initial_bundle_changed']
    for ctx in s['contexts']:
        for item in ctx['items']:
            snapshot=read_snapshot(item['parent_path'])
            assert snapshot['document_version']==item['document_version']
            assert snapshot['canonical_text'][item['start']:item['end']]==item['quote']
repo=ReadOnlyRepository(OUT/'revision_drafts/review.sqlite3')
with repo.connect() as c:
    assert c.execute("SELECT COUNT(*) FROM revision_drafts WHERE status='proposed'").fetchone()[0]==18
    counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in (
        'revision_votes','validator_votes','review_events','jobs','import_outbox','graph_outbox','ontology_publish_outbox','request_attempts')}
    assert not any(counts.values())
    assert c.execute('SELECT COUNT(*) FROM candidate_revisions WHERE revision>1').fetchone()[0]==0
for candidate in read(POOL/'candidates.json'):assert repo.get_revision(candidate['candidate_id'])==candidate
pilot_plans=[by_candidate[p['candidate_id']] for p in plans if p['candidate_id'] in by_candidate]
report={**summary,'verification_passed':True,'wire_plan_scope':'all_160_frozen_development_candidates',
    'wire_drafts_in_pilot':pilot_plans,'wire_drafts_outside_pilot':18-len(pilot_plans),
    'original_statements_qualifiers_and_labels_not_overwritten':True,'drafts_proposed_not_approved_or_applied':True,
    'source_supplement_spans_verified':True,'ocr_original_page_missing':['PILOT-011','PILOT-036'],
    'priority_question_ids':list(QUESTIONS),'read_only_workbook_inspection':True,
    'repository_counts':counts,'new_api_requests':0,'regression_tests':16,
    'note':'18 AI revise preferences and 18 wire drafts are different sets; no accuracy calculated from AI labels.'}
write(OUT/'verification.json',report)
files=[ROOT/'evaluation/preannotation_followup.py',ROOT/'extract_triplets/qualifier_wire.py',ROOT/'extract_triplets/candidate_contract.py',
    ROOT/'tests/test_preannotation_followup.py',Path(__file__)]
write(OUT/'code_fingerprints.json',{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
print(json.dumps({'verification_passed':True,'wire_drafts':18,'pilot_wire_drafts':pilot_plans,'new_api_requests':0,'human_gold':0},ensure_ascii=False))
