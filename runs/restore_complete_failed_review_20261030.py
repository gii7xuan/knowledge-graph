"""Repair one verified disk-failed review without sending any model request."""
from pathlib import Path
import json
from review_agent.cli import _configured_validators
from review_agent.completed_audit_recovery import restore_completed_audit
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config()
identity='cand_466b9468afe0efc3434c';audit_id='audit_6c1adc4cfd4f406990596b9cbcd5afb7'
candidate=repo.get_revision(identity)
if candidate['run_id']!='run_chapter1_20261028_resume1':raise RuntimeError('unexpected run')
manifest=repo.get_run(candidate['run_id'])
orchestrator=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(manifest['ontology_path'])),_configured_validators(config),config=config)
result=restore_completed_audit(repo,orchestrator,identity,audit_id,
    reason='用户继续处理候选修订与人工分歧：恢复磁盘 I/O 错误后未提交的最新完整三席审核，仅采用原服务器检查点、冻结提案与原票；无模型调用，不自动准入。')
out=ROOT/'runs/candidate_revision_resolution_20261030/failed_complete_audit_restoration.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result.get(k) for k in ('candidate_id','audit_id','revision','committed','model_calls','source_support_result','workflow_state','independent_review_completed','original_votes_unchanged')},ensure_ascii=False))
