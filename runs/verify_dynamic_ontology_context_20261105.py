"""Read-only Dev30 context replay. Does not send requests or update review data."""
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
from datetime import datetime

from review_agent.ontology_context import select_context, policy_for
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from evidence.hierarchical_retriever import retrieve_initial
from evidence.verification import verify_evidence_bundle
from evidence.units import enrich_bundle
from document.hierarchy_index import load_document_index

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/dev30_v62_rerun_20261105'
OUT = ROOT / 'outputs/dynamic_ontology_context_20261105'


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    protected = [SOURCE / 'review.sqlite3', SOURCE / 'review.sqlite3-wal', SOURCE / 'settings.json',
                 ROOT / 'runs/v3/review.sqlite3', ROOT / 'runs/v3/review.sqlite3-wal',
                 ROOT / 'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx',
                 ROOT / 'outputs/dev30_20261031/sample_data.json',
                 ROOT / 'prompts/triple_validator_v6_2.txt']
    before = {str(p.relative_to(ROOT)): fingerprint(p) for p in protected if p.exists()}
    conn = sqlite3.connect((SOURCE / 'review.sqlite3').as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    manifest = json.loads(conn.execute('SELECT manifest_json FROM runs LIMIT 1').fetchone()[0])
    guard = OntologyGuard(load_snapshot(manifest['ontology_path'], manifest['ontology_hash']))
    document = load_document_index(manifest['document_path'], manifest.get('source_index_hash'), require_verified=True)
    frozen = {}
    for row in conn.execute('SELECT proposal_json FROM proposals ORDER BY round_id'):
        p = json.loads(row[0]); frozen[p['candidate_id']] = p
    sample = json.loads((ROOT / 'outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
    names = {b['candidate_id']: b['sample_id'] for b in sample['bindings']}
    population = json.loads((SOURCE / 'candidate_manifest.json').read_text(encoding='utf-8'))['candidates']
    policy = policy_for(json.loads((ROOT / 'config/review_agent.json').read_text(encoding='utf-8')))
    rows = []
    for binding in population:
        cid = binding['candidate_id']
        candidate = json.loads(conn.execute('SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?',
                                           (cid, binding['revision'])).fetchone()[0])
        p = frozen.get(cid)
        if p:
            assert p['candidate'] == candidate
            bundle = p['evidence_bundle']
        else:
            bundle = enrich_bundle(verify_evidence_bundle(retrieve_initial(candidate, document, max_items=6), document, candidate), document, candidate)
        context = select_context(guard, candidate, bundle, policy)
        assert context == select_context(guard, candidate, bundle, policy)
        assert context['absence_confirmed'] is False
        encoded = json.dumps(context, ensure_ascii=False)
        assert len(encoded) <= policy['limits']['max_chars']
        if p:
            baseline = len(json.dumps(p['ontology_catalog'], ensure_ascii=False))
        else:
            from schema.relation_registry import RelationRegistry
            baseline = len(json.dumps({**RelationRegistry(guard.ontology).reference_catalog(),
                                       'classes': list(guard.classes.values())}, ensure_ascii=False))
        rows.append({'sample_id': names.get(cid), 'candidate_id': cid, 'revision': candidate['revision'],
                     'evidence_source': 'frozen_previous_request' if p else 'offline_initial_retrieval_not_previously_sent',
                     'full_catalog_chars': baseline, 'context_chars': len(encoded), 'context_utf8_bytes': len(encoded.encode()),
                     'reduction_percent': round(100 * (1 - len(encoded) / baseline), 2),
                     'properties': len(context['properties']), 'classes': len(context['classes']),
                     'required_properties': len(context['retrieval']['required_property_ids']),
                     'context_hash': hashlib.sha256(encoded.encode()).hexdigest(),
                     'global_absence_confirmed': context['absence_confirmed']})
    conn.close()
    after = {str(p.relative_to(ROOT)): fingerprint(p) for p in protected if p.exists()}
    assert before == after, 'protected source changed during read-only replay'
    summary = {'updated_at': datetime.now().isoformat(), 'offline_only': True, 'model_requests': 0,
               'review_database_writes': 0, 'historical_data_unchanged': before == after,
               'candidates': len(rows), 'previously_requested': sum(r['evidence_source']=='frozen_previous_request' for r in rows),
               'full_catalog_chars_median': statistics.median(r['full_catalog_chars'] for r in rows),
               'context_chars_min': min(r['context_chars'] for r in rows),
               'context_chars_median': statistics.median(r['context_chars'] for r in rows),
               'context_chars_max': max(r['context_chars'] for r in rows),
               'median_reduction_percent': statistics.median(r['reduction_percent'] for r in rows),
               'policy': policy, 'protected_fingerprints': before, 'rows': sorted(rows, key=lambda r:r['sample_id'] or '')}
    (OUT / 'offline_replay.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 审核动态本体上下文离线核验', '',
             f"30条样本均完成离线构造；其中{summary['previously_requested']}条复用上一轮冻结证据，其余4条使用本地初始证据检索。没有调用模型，没有重跑审核。", '',
             f"完整目录中位{summary['full_catalog_chars_median']:g}字符；新版上下文中位{summary['context_chars_median']:g}字符，范围{summary['context_chars_min']}—{summary['context_chars_max']}字符，中位减少{summary['median_reduction_percent']:.1f}%。", '',
             '这里统计的是本体上下文字符数，不是完整请求token数；尚未测量模型延迟和审核准确率。', '',
             '通常召回8个关系；正式ID、所有同名竞争关系、端点类型、domain/range类型和祖先强制保留。硬上限24个关系、64个类、32000字符，超预算停止，不截断定义。', '',
             '检索在完整冻结本体上执行。模型只看到子集时，不允许把检索未命中当成全局缺口；真实意见保留，新增类/属性的扩展准入由程序拦截。', '',
             'V6.3独立冻结提示词、检索政策、上下文及哈希；V6.2及更早的请求、缓存与恢复仍使用旧版本。', '',
             '|样本|原目录字符|新上下文字符|减少|关系数|类数|强制关系数|',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in summary['rows']:
        lines.append(f"|{r['sample_id']}|{r['full_catalog_chars']}|{r['context_chars']}|{r['reduction_percent']}%|{r['properties']}|{r['classes']}|{r['required_properties']}|")
    lines += ['', '原人工标注表、样本绑定、旧实验数据库、生产数据库和V6.2提示词的文件哈希在本次离线核验前后保持一致。详细哈希与样本数据见offline_replay.json。']
    (OUT / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'rows','protected_fingerprints','policy'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
