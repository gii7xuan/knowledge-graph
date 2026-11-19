"""Reclassify recorded outcomes into human handoff without new inference."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .freeze_shared_ontology import ROOT,read,write
from .gpt_five_group import GROUPS
from .gpt_reference import compare
from .human_handoff import normalize,technical_failure
from .metrics import fixed_candidate_metrics


def reclassify(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if not source.is_relative_to(ROOT/'outputs') or not output.is_relative_to(ROOT/'outputs') or output.exists():
        raise ValueError('use recorded input and a new output directory under outputs')
    source_report=read(source/'comparison_report.json');verification=read(source/'final_verification.json')
    for name,digest in verification['database_sha256'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:raise ValueError('original database changed')
    inputs=read(source/'model_inputs.json');reference=read(source/'gpt_reference.json')
    output.mkdir();groups={};metrics={};hashes={};queues={}
    for group in GROUPS:
        path=source/(group+'_predictions.json');hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        rows=[];queue=[]
        for recorded,sample in zip(read(path),inputs):
            raw=deepcopy(recorded);archive=None
            if raw.get('status')=='skipped_unknown':
                archive=Path(raw['partial_results_archive']).resolve()
                if not archive.is_relative_to(ROOT/'outputs'):raise ValueError('archive outside experiment outputs')
                original=read(archive)
                if original['candidate_id']!=raw['candidate_id']:raise ValueError('archive candidate mismatch')
                hashes[str(archive)]=hashlib.sha256(archive.read_bytes()).hexdigest()
                raw=deepcopy(original['partial_results_excluded_from_comparison'][group])
            if raw['binding']!=sample['reference_binding']:raise ValueError('reference packet binding changed')
            blocked_initial=bool(archive and raw.get('status')=='not_started')
            failed=technical_failure(raw) or blocked_initial
            reason='initial_committee_timeout_or_unavailable' if blocked_initial else None
            if not reason and failed and raw.get('status')=='timeout':reason='timeout_judgment_failed'
            row=normalize(raw,reason=reason,technical=failed)
            row['source_prediction_file']=str(path)
            if archive:row['restored_recorded_result_from']=str(archive)
            rows.append(row)
            if row['human_review_required']:
                queue.append({'sample_id':row['sample_id'],'candidate_id':row['candidate_id'],
                    'reason':row['handoff_reason'],'technical_failure':row['technical_failure'],
                    'original_execution_status':row['execution_status'],'candidate':sample['candidate'],
                    'initial_evidence_bundle':sample['initial_evidence_bundle'],
                    'recorded_prediction':row,'request_audit_source':str(source),
                    'human_adjudication_status':'pending'})
        groups[group]=rows;queues[group]=queue
        operational=fixed_candidate_metrics(rows)
        metrics[group]={'reference_comparison':compare(rows,reference),'operational':operational,
            'deployment_usage':source_report['groups'][group]['deployment_usage']}
        assert sum(operational['outcomes'].values())==len(inputs)
        assert len(queue)==operational['outcomes']['manual']
        write(output/(group+'_predictions.json'),rows)
        write(output/(group+'_human_review_queue.json'),queue)
    record={'version':'human_handoff_policy_reclassification_v1','source_run':str(source),
        'user_clarification':'semantic uncertainty and timeout/technical judgment failure are human-review outcomes; continue other candidates',
        'policy_change_is_posthoc_and_explicit':True,'population':len(inputs),'excluded_candidates':0,
        'new_provider_requests':0,'unknown_requests_resent':0,'source_prediction_sha256':hashes,
        'source_databases_unchanged':True,'source_database_sha256':verification['database_sha256'],
        'source_execution_code_snapshot':str(source/'code_fingerprints.json'),
        'rescoring_code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__),ROOT/'evaluation/human_handoff.py',ROOT/'evaluation/metrics.py')},
        'human_gold_count':0,'label_source':'gpt_preannotation','groups':metrics,
        'physical_usage_unchanged':{k:v for k,v in source_report['usage'].items() if k!='requests'}}
    write(output/'comparison_report.json',record)
    write(output/'gpt_reference.json',reference)
    write(output/'unknown_requests.json',read(source/'unknown_requests.json'))
    names={'b0_all_keep':'B0 全部保留','b1_single':'B1 DeepSeek','b2_majority':'B2 多数票','b3_fixed_workflow':'B3 固定流程','agent':'完整Agent'}
    lines=['# 按人工兜底口径重计的40条实验结果','',
        '40条全部保留。最终终态为自动通过、自动拒绝、待人工；超时、格式失败、预算内判断失败等是待人工的原因，不再作为排除候选的理由。尚未执行的队列项另记待处理，本轮为0。','',
        '本报告按用户澄清后的口径对既有记录重计，未新增模型请求，未重发未知请求；原执行记录与旧报告保留。参考标签仍为GPT标注，转人工队列不代表已经取得人工裁决。','',
        '| 组别 | 自动通过 | 自动拒绝 | 待人工 | 转人工率 | 其中：技术原因转人工 | 自动决策覆盖率 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for g,m in metrics.items():
        o=m['operational'];c=o['outcomes']
        lines.append(f"| {names[g]} | {c['accept']} | {c['reject']} | {c['manual']} | {c['manual']}/40 = {o['manual_rate']['value']:.1%} | {o['technical_failure_handoff_count']}/40 = {o['technical_incompletion_rate']['value']:.1%} | {o['automatic_decision_coverage']['value']:.1%} |")
    lines+=['','技术原因转人工是转人工的子集，不能作为互斥终态再次相加。B0不执行模型审核，其全部保留建议不因其他方法超时而改变；这些都是研究建议，实际图谱写入仍为0。','',
        '## 分维度GPT参考一致率','',
        '| 组别 | 来源一致/可比 | 表达一致/可比 |','|---|---:|---:|']
    for g,m in metrics.items():
        axis=m['reference_comparison']['metrics']
        cell=lambda k:f"{axis[k]['agreement_count']}/{axis[k]['comparable_count']}" if axis[k]['comparable_count'] else '—'
        lines.append(f"| {names[g]} | {cell('source_support')} | {cell('expression_status')} |")
    usage=source_report['usage']
    lines+=['','第27、40条也计入总体。各方法已经合法返回的维度判断保留；未返回的判断不补造。分维度一致率仍只以双方明确标签为可比分母，需结合覆盖率理解，不能当成人工准确率。','',
        f"累计真实请求仍为{usage['unique_provider_attempts']}次，已知{usage['known_tokens']:,} token，未知结果请求{usage['unknown_requests']}次。未知计费继续单独记录，与候选转人工互不替代。",'',
        '## 待人工任务','',
        '完整Agent待人工清单见 agent_human_review_queue.json；每条保留原候选、初始证据、原执行状态及转人工原因。它是本地待审清单，尚未执行实际人工审核。','',
        '准入精确率、正确候选保留率、错误放行率需要明确的可接纳参考裁决；现有参考尚未定义该二值真值，因此仍记未评估。转人工率本身不依赖先取得这类真值。']
    (output/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();r=reclassify(args.source,args.output)
    print(json.dumps({'output':args.output,'new_provider_requests':0,'population':r['population'],
        'outcomes':{g:m['operational']['outcomes'] for g,m in r['groups'].items()}},ensure_ascii=False))


if __name__=='__main__':main()
