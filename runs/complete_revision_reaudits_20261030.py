"""Complete mandatory committee review when a new revision's planner stopped pre-vote."""
from copy import deepcopy
from pathlib import Path
import json
import time
from document.hierarchy_index import load_document_index
from review_agent.cli import _configured_validators
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030/required_reaudit'
OUT.mkdir(exist_ok=True)
if __name__=='__main__':
    original=load_config();config=deepcopy(original)
    config['planner'].update(enabled=False,strategy='deterministic')
    config['consensus']['max_rounds']=1
    config['budget']['max_retries_per_request']=0
    (OUT/'effective_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    applied=json.loads((ROOT/'runs/review_followup_20261030/revisions/status.json').read_text(encoding='utf-8'))
    rows=[r for r in applied['results'] if r.get('status')=='revision_applied']
    state={'status':'running','selected':len(rows),'processed':0,'independent_completed':0,'results':[],'started_at_epoch':time.time()}
    write_status_snapshot(OUT/'status.json',state)
    for row in rows:
        current=repo.get_candidate(row['candidate_id']);previous=repo.get_audit(row['candidate_id'])
        if previous['rounds']:
            result={'candidate_id':row['candidate_id'],'status':'existing_frozen_round_preserved'}
        else:
            candidate=repo.get_revision(row['candidate_id']);manifest=repo.get_run(candidate['run_id'])
            account=repo.case_status(repo.ensure_case(candidate['candidate_id']))
            additions={k:max(0,account['usage'].get(k,0)+v-account['limits'][k]) for k,v in {'llm_calls':6,'tokens':100000}.items()}
            additions={k:v for k,v in additions.items() if v}
            if additions:
                repo.authorize_case_budget(candidate['candidate_id'],additions,reason='补齐已提交新版本的强制独立审核前置工作；原规划停滞且没有委员票，门槛保持原三席',
                    actor=original.get('local_operator','local_operator'),authorization_key='required-review:'+candidate['candidate_id']+':'+str(candidate['revision']))
            document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
            guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
            orchestrator=ReviewOrchestrator(repo,guard,_configured_validators(config),config=config)
            judged=orchestrator.audit_candidate(candidate['candidate_id'],candidate['revision'],document,
                context={'configured_strategy':original['planner']['strategy'],'executed_strategy':'deterministic',
                    'operator_reason':'mandatory independent review after planner stopped before any vote','supersedes_no_vote_audit':previous['audit_id']})
            write_status_snapshot(OUT/(candidate['candidate_id']+'.json'),judged)
            result={k:judged.get(k) for k in ('candidate_id','audit_id','revision','independent_review_completed','review_completion_status','decision','workflow_state')}
            if judged.get('independent_review_completed'): state['independent_completed']+=1
        state['processed']+=1;state['results'].append(result);write_status_snapshot(OUT/'status.json',state)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    state.update(status='completed',finished_at_epoch=time.time());write_status_snapshot(OUT/'status.json',state)
