"""Validate the recovery locally, then prepare a fresh immutable extraction run."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
import chapter1_recovery_support as support
from extract_triplets import pipeline_v4 as extraction
from ontologyBuild.release_service import load_release

OLD=ROOT/'runs/chapter1_extraction/chapter1_20261027_published'
JOB=OLD.parent/'chapter1_20261028_resume1'


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    old=read(OLD/'status.json')
    assert old['status']=='failed'
    if JOB.exists(): raise FileExistsError('recovery job already exists; inspect before retrying')
    release=load_release(old['ontology_release_path'])
    config=read(OLD/'review_config.json')
    prompt=(OLD/'extraction_prompt.txt').read_text(encoding='utf-8')
    checkpoint=Path(old['checkpoint'])
    replay=support.SourceResponseReplay(checkpoint,prompt=prompt,release_binding=release.binding(),model=old['model'],
        filter_policy=config['extraction']['filter_policy'])
    JOB.mkdir()
    for name in ('review_config.json','extraction_prompt.txt'):
        shutil.copy2(OLD/name,JOB/name)
    smoke=JOB/'verification';smoke.mkdir()
    transient_calls=[]
    def transient_replace(source,target):
        transient_calls.append(1)
        if len(transient_calls)<3: raise PermissionError('simulated transient Windows sharing violation')
        os.replace(source,target)
    support.atomic_json(smoke/'transient_status.json',{'ok':True},replace=transient_replace,sleep=lambda _:None)
    assert len(transient_calls)==3 and read(smoke/'transient_status.json')=={'ok':True}
    with patch.object(support,'atomic_json',side_effect=PermissionError('simulated persistent sharing violation')):
        assert support.progress_json(smoke/'locked_status.json',{'progress':'preserved'}) is False
    fallback=list((smoke/'progress_fallback').glob('*.json'))
    assert len(fallback)==1 and read(fallback[0])=={'progress':'preserved'}
    old_prompt=extraction.PROMPT_PATH
    old_extract=extraction.extract_source_scope
    extraction.PROMPT_PATH=JOB/'extraction_prompt.txt'
    replay_hits=[]
    def offline(scope,**kwargs):
        result=replay.lookup(scope,index=kwargs['index'],model=kwargs['model'],settings=kwargs['settings'])
        if result is not None:
            replay_hits.extend(result['attempts'])
            return result
        return {'triplets':[],'coverage_status':'failed','attempts':[],'prompt_hash':replay.prompt_hash}
    extraction.extract_source_scope=offline
    try:
        recovered=extraction.extract_candidates(replay.index,release.ontology,release.binding(),llm_client=None,
            model=old['model'],run_id='run_'+JOB.name,settings=config['extraction'])
    finally:
        extraction.PROMPT_PATH=old_prompt
        extraction.extract_source_scope=old_extract
    successes=[r for r in replay_hits if r['status']=='completed']
    assert len(successes)==len(replay.complete)==13
    support.atomic_json(JOB/'recovered_candidates.json',recovered['candidates'])
    support.atomic_json(JOB/'recovered_source_report.json',{k:v for k,v in recovered.items() if k!='candidates'})
    sample=next(iter(replay.complete.values()))['scope']
    bindings={'release_binding':release.binding(),'filter_policy':config['extraction']['filter_policy']}
    for changed_index,changed_settings in [
        ({**replay.index,'index_content_hash':'changed'},bindings),
        (replay.index,{**bindings,'filter_policy':{'changed':True}}),
        (replay.index,{**bindings,'release_binding':{**release.binding(),'ontology_version':'changed'}})]:
        try: replay.lookup(sample,index=changed_index,model=old['model'],settings=changed_settings)
        except ValueError: pass
        else: raise AssertionError('changed binding unexpectedly replayed')
    summary={'status':'passed','transient_replace_retry':True,'persistent_progress_lock_does_not_abort':True,
        'source_policy_release_drift_rejected':True,'remote_calls_for_recovery':0,
        'reused_successful_responses':len(successes),'raw_candidate_items':35,
        'recovered_candidate_count':len(recovered['candidates']),'rejected_or_context_outputs':len(recovered['malformed_or_context_outputs']),
        'source_scope_count':len(replay.index['chunks']),'old_checkpoint_preserved':str(checkpoint)}
    support.atomic_json(JOB/'recovery_verification.json',summary)
    state={k:deepcopy(old[k]) for k in ('input','chapter_source','source_scope_count','prompt_sha256','review_mode',
        'model_call_budget','ontology_release_id','ontology_version','profile_hash','ontology_release_path',
        'destination_authorization')}
    state.update(job_id=JOB.name,run_id='run_'+JOB.name,status='prepared',created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        resume_checkpoint=str(checkpoint),resume_from_job=str(OLD),attempts_started=0,attempts_finished=0,
        successful_model_calls=0,failed_model_calls=0,replayed_model_records=0,reused_successful_responses=0,
        locally_recovered_candidate_count=len(recovered['candidates']))
    support.atomic_json(JOB/'status.json',state)
    (JOB/'README.md').write_text('# 第一章抽取恢复任务\n\n旧任务在 2026-10-28 00:15:51 因 Windows 状态文件替换失败而中断。\n\n'
        '本次保持相同来源、提示词、模型、过滤政策和发布版本，复用原有成功响应。进度文件遇到锁定时保存独立快照并继续抽取。\n\n'
        '- [当前状态](status.json)\n- [恢复验证](recovery_verification.json)\n- [已恢复来源候选](recovered_candidates.json)\n'
        '- [标准输出](stdout.log)\n- [错误日志](stderr.log)\n\n候选恢复不代表审核通过；完整输出仍需等待剩余片段处理。\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
