"""Offline-only, immutable V8 revalidation and reaggregation of existing returns."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import bind_packet,annotation_template,quality_report
from review_agent.consensus import Committee,aggregate_votes
from review_agent.contracts import sha256_json
from review_agent.review_observability import assess_vote,outcome,policy,redact
from review_agent.mapping_trace import statistics

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v8_rerun_20261109'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def committee(proposal):
    p=proposal['committee_policy']
    return Committee([m['id'] for m in proposal['committee_members']],p['required_votes'],p['minimum_distinct_families'],proposal['committee_version'])


def save(directory,name,value,*,jsonl=False):
    text=(''.join(json.dumps(redact(row),ensure_ascii=False,sort_keys=True)+'\n' for row in value) if jsonl else
        json.dumps(redact(value),ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    target=directory/name
    if target.exists():
        if target.read_text(encoding='utf-8')!=text:raise ValueError('derived output differs; use a new directory: '+str(target))
    else:target.write_text(text,encoding='utf-8')


def derive(source,directory):
    source=source.resolve();directory=directory.resolve()
    if directory==source or directory.is_relative_to(source) or not directory.is_relative_to(ROOT/'outputs'):
        raise ValueError('derived output must be a new separate directory under outputs')
    directory.mkdir(parents=True,exist_ok=True)
    watched=[p for p in source.rglob('*') if p.is_file() and p.suffix not in {'.log','.jpg','.png'}]
    inputs={str(p.relative_to(ROOT)):digest(p) for p in sorted(watched)}
    code={str(p.relative_to(ROOT)):digest(p) for folder in ('review_agent','evidence','evaluation','prompts','specs')
        for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and p.suffix in {'.py','.json','.txt'}}
    code[str(Path(__file__).relative_to(ROOT))]=digest(Path(__file__))
    manifest={'mode':'offline_revalidation_and_reaggregation','source':str(source),'inputs':inputs,'code':code,
        'policy':policy(),'new_model_calls':0,'production_written':False,'historical_records_changed':False}
    manifest['derivation_id']=sha256_json(manifest)
    save(directory,'manifest.json',manifest)
    repo=ReadOnlyRepository(source/'review.sqlite3');bindings=read(source/'sample_bindings.json')
    known=read(source/'frozen_trace_diagnosis.json')
    affected={(r['audit_id'],r['proposal_digest'],v) for r in known['cases'] for v in r['affected_voter_ids']}
    original_eligible={(r['audit_id'],r['proposal_digest']):r['original_eligible_voter_ids'] for r in known['cases']}
    vote_rows=[];proposals=[];decisions=[];selected=[];packets=[];judgeb=[]
    current=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    current_hashes={b['candidate_id']:sha256_json(current.get_revision(b['candidate_id'])) for b in bindings}
    for b in bindings:
        decision=repo.latest_decision(b['candidate_id']) or {}
        audit=repo.get_audit(b['candidate_id'],decision.get('audit_id'))
        rounds=audit.get('rounds') or []
        chosen=next((p for p in rounds if p['proposal_digest']==decision.get('selected_proposal_digest')),rounds[-1] if rounds else None)
        if not rounds:rounds=[{**audit['context'],'candidate':repo.get_revision(b['candidate_id']),
            'evidence_bundle':{'items':[]},'round_id':None,'proposal_digest':None}]
        for p in rounds:
            votes=[v for v in audit.get('votes',[]) if v.get('proposal_digest')==p.get('proposal_digest') and v.get('round_id')==p.get('round_id')]
            c=committee(p)
            new=aggregate_votes(votes,c,proposal=p,proposal_digest=p.get('proposal_digest')) if p.get('proposal_digest') else {}
            derived=outcome(p,votes,c,audit_status=audit['status'],context={**audit['context'],'final_gaps':p.get('gaps') or {}},
                decision={**decision,'consensus':new},stop_reason=decision.get('stop_reason'))
            is_selected=bool(chosen and p.get('proposal_digest')==chosen['proposal_digest']) or chosen is None
            identity={'sample_id':b['sample_id'],'candidate_id':b['candidate_id'],'revision':audit['revision'],
                'audit_id':audit['audit_id'],'round_id':p.get('round_id'),'proposal_digest':p.get('proposal_digest'),
                'evidence_hash':p.get('evidence_bundle',{}).get('evidence_hash'),'ontology_context_hash':p.get('ontology_context_hash')}
            for v in votes:
                assessment=assess_vote(v,p)
                key=(audit['audit_id'],p['proposal_digest'],v['voter_id'])
                vote_rows.append({**identity,**assessment,'known_trace_defect':key in affected,
                    'old_validation':{'response_status':v.get('response_status'),
                        'eligible':v['voter_id'] in original_eligible.get(key[:2],p.get('consensus',{}).get('eligible_voter_ids',[])),
                        'known_error':'field correction differs from stored model fields' if key in affected else None},
                    'new_validation':{'eligible':v['voter_id'] in new.get('eligible_voter_ids',[]),'errors':assessment['errors']},
                    'raw_reference':{'database':str(source/'review.sqlite3'),'table':'validator_votes',**identity,'voter_id':v['voter_id']},
                    'field_diff':(v.get('format_normalization') or {}).get('changes',[]),
                    'correction_trace':v.get('correction_trace'),'response_id':v.get('response_id'),
                    'finish_reason':v.get('finish_reason'),'requested_model_id':v.get('model_id'),'returned_model_id':v.get('returned_model_id')})
            entry={**identity,'selected_current_packet':is_selected,'historical_decision_selected':bool(chosen and decision.get('selected_proposal_digest')==p.get('proposal_digest')),
                'three_seats_complete':set(new.get('eligible_voter_ids',[]))==set(c.voter_ids),
                'eligible_voter_ids':new.get('eligible_voter_ids',[]),'outcome':derived,
                'historical_workflow':decision.get('workflow_state'),'historical_independent_review_completed':decision.get('independent_review_completed',False),
                'missing_steps':derived['blocking_reasons'],'new_model_calls':0,'old_decision_rewritten':False}
            decisions.append(entry);proposals.append({k:v for k,v in entry.items() if k not in {'outcome','missing_steps'}})
            if is_selected:
                selected.append(entry)
                packet=bind_packet(p['candidate'],p,p['evidence_bundle'],p.get('review_scope_policy') or audit['context'].get('admission_policy',{}).get('scope_policy') or {},
                    system={'auto_admitted':False,'recommendation':derived['recommendation'],
                        'prediction_observed':bool(chosen),'mapping_completed':derived['dimension_results']['mapping']['status']=='valid',
                        'revision_requested':derived['recommendation']=='revise'})
                packet['sample_id']=b['sample_id'];packets.append(packet)
    # Existing three DEV-016 trials, referenced rather than rewritten.
    from review_agent.validators import _parse_content
    from review_agent.validator_contracts import contract_for
    from review_agent.response_corrections import schema_issues
    for trial in read(source/'frozen_repeat_report.json')['trials_detail']:
        v=next(v for v in trial['votes'] if v['voter_id']=='judge_b');raw=_parse_content(v['raw_response'])
        conflicts=[{'path':'/expression_findings/'+str(i)+'/kind','expected':'semantic_error when meaning_effect=changed',
            'actual_type':type(f.get('kind')).__name__,'actual_value':f.get('kind'),'meaning_effect':f.get('meaning_effect')}
            for i,f in enumerate(raw.get('expression_findings',[])) if f.get('meaning_effect')=='changed' and f.get('kind')!='semantic_error']
        judgeb.append({'sample_id':'DEV-016','trial':trial['trial'],'request_succeeded':True,'response_status':v['response_status'],
            'response_contract_version':v.get('response_contract_version'),'raw_response_hash':sha256_json(v['raw_response']),
            'raw_reference':str(source/('frozen_repeat_report.json' if trial['trial']==0 else 'repeat_'+str(trial['trial'])+'/result.json')),
            'schema_issues':[{'path':i['parts'],'kind':i['kind']} for i in schema_issues(raw,contract_for(v['response_contract_version']).RESPONSE_SCHEMA)],
            'semantic_errors':contract_for(v['response_contract_version']).validate_response(raw),'conflict_fields':conflicts,
            'error_category':'semantic_consistency','failed_stage':'semantic_contract','requested_model_id':v.get('model_id'),
            'returned_model_id':v.get('returned_model_id'),'finish_reason':v.get('finish_reason'),
            'resolution':'one explanation-only clarification, original invalid ballot remains ineligible; no model replacement',
            'judgment_stability':'not_evaluable'})
    known_rows=[r for r in vote_rows if r['known_trace_defect']]
    if len(known_rows)!=known['affected_votes']:raise ValueError('known trace-defect population changed')
    complete=[r for r in selected if r['three_seats_complete']]
    recovered=[r for r in selected if not r['historical_independent_review_completed'] and r['three_seats_complete']]
    index={'unit':'candidate/revision/audit/round/proposal_digest','entries':proposals,
        'selected_full_roster_proposals':len(complete),'selected_full_roster_distinct_candidates':len({r['candidate_id'] for r in complete}),
        'selected_full_roster_candidate_versions':len({(r['candidate_id'],r['revision']) for r in complete}),
        'all_round_full_roster_proposals':sum(r['three_seats_complete'] for r in proposals),
        'recovered_selected_full_roster_candidates':[r['sample_id'] for r in recovered],
        'known_affected_votes':len(known_rows),'known_affected_candidates':len({r['candidate_id'] for r in known_rows}),
        'known_affected_candidate_versions':len({(r['candidate_id'],r['revision']) for r in known_rows}),
        'known_affected_proposals':len({r['proposal_digest'] for r in known_rows}),
        'known_affected_now_eligible':sum(r['new_validation']['eligible'] for r in known_rows)}
    categories=Counter();seats=Counter();stages=Counter()
    for row in vote_rows:
        for e in row['errors']:categories[e['category']]+=1;seats[row['voter_id']+':'+e['category']]+=1;stages[e['stage']]+=1
    breakdown={'unit':'validation_error_occurrences; a vote can have multiple errors','by_category':dict(categories),
        'by_seat_category':dict(seats),'by_stage':dict(stages),'invalid_selected_votes':sum(not r['new_validation']['eligible'] for r in vote_rows),
        'candidate_blockers':dict(Counter(k for r in selected for k in r['missing_steps']))}
    summary={'population':len(selected),'proposals':len(proposals),'votes':len(vote_rows),'new_model_calls':0,
        'execution_counts':dict(Counter(r['outcome']['execution_status'] for r in selected)),
        'full_roster_selected_candidates':len(complete),'historical_independent_review_completed':sum(r['historical_independent_review_completed'] for r in selected),
        'dimension_completed':{a:sum(r['outcome']['dimension_results'][a]['judgment_complete'] for r in selected) for a in ('source','expression','mapping','scope')},
        'four_dimensions_completed':sum(r['outcome']['review_completed'] for r in selected),
        'recommendations':dict(Counter(r['outcome']['recommendation'] for r in selected)),'actions_taken':{'none':len(selected)},
        'mode':'offline_revalidation_and_reaggregation','current_human_labels':0,'production_written':False}
    quality=quality_report(packets)
    for name,value,jsonl in [('vote_revalidation.jsonl',vote_rows,True),('proposal_index.json',index,False),
        ('decision_reaggregation.jsonl',decisions,True),('failure_breakdown.json',breakdown,False),
        ('judge_b_diagnosis.json',judgeb,False),('summary.json',summary,False),('quality_report.json',quality,False),
        ('mapping_process_statistics.json',statistics(r['outcome']['mapping_process'] for r in selected),False),
        ('review_packets.json',{'samples':packets},False),('annotation_template.json',{'annotations':[annotation_template(p) for p in packets]},False)]:save(directory,name,value,jsonl=jsonl)
    lines=['# V8 离线重验与重聚合','',f"派生ID：{manifest['derivation_id']}",'',
        '仅重验已有返回，模型调用0次；历史票、历史决定和生产数据保持原样。',
        f"已知误判35张票：{index['known_affected_now_eligible']}张恢复有效；覆盖{index['known_affected_candidates']}个候选、{index['known_affected_candidate_versions']}个候选版本、{index['known_affected_proposals']}份提案。",
        f"当前选定提案三席完整{len(complete)}份，对应{index['selected_full_roster_distinct_candidates']}个不同候选；不混合不同轮次。",'',
        '三席完整的定义：同一冻结提案、候选版本、证据/本体/规则/提示词绑定通过，三个固定席位契约有效、证据引用有效且模型身份和家族满足原门槛。席位不代表统计独立。',
        f"四维全部可判定{summary['four_dimensions_completed']}条；当前四维人工标签0条，质量指标暂不可评估。",'',
        'judge_b 三轮结构合法，均为 changed meaning 与 compound_entity 等非 semantic_error 类型自相矛盾。保留原始票无效；判断稳定性不可评估。',
        '35票逐张解释见 vote_revalidation.jsonl；15份提案去重见 proposal_index.json；终态和缺步骤见 decision_reaggregation.jsonl。','',
        '|样本|执行终态|三席完整|原文|表达|映射|范围|建议|','|---|---|---|---|---|---|---|---|']
    for r in selected:
        o=r['outcome'];lines.append('|'+ '|'.join(map(str,[r['sample_id'],o['execution_status'],r['three_seats_complete'],
            *[o['dimension_results'][a]['status'] for a in ('source','expression','mapping','scope')],o['recommendation']]))+'|')
    text='\n'.join(lines)+'\n';target=directory/'report.md'
    if target.exists() and target.read_text(encoding='utf-8')!=text:raise ValueError('report differs; use a new experiment')
    if not target.exists():target.write_text(text,encoding='utf-8')
    if inputs!={str(p.relative_to(ROOT)):digest(p) for p in sorted(watched)}:raise ValueError('historical input changed during offline analysis')
    if current_hashes!={b['candidate_id']:sha256_json(current.get_revision(b['candidate_id'])) for b in bindings}:raise ValueError('production candidate changed')
    save(directory,'protection_verification.json',{'historical_hashes_unchanged':True,'production_candidate_hashes_unchanged':True,'source_open_mode':'ro','model_calls':0})
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/('dev30_v8_revalidated_'+datetime.now().strftime('%Y%m%d_%H%M%S')))
    args=parser.parse_args()
    with ExitStack() as stack:
        for name in ('OpenAICompatibleClient','MockLLMClient'):
            stack.enter_context(patch('review_agent.llm_clients.'+name+'.complete_json',side_effect=RuntimeError('offline mode prohibits all model calls')))
        result=derive(args.source,args.output)
    print(json.dumps({'output':str(args.output),**result},ensure_ascii=False))


if __name__=='__main__':main()
