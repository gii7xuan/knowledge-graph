"""Read-only verification of all DEV-020 stages and the exact unresolved request."""
from datetime import datetime, timezone, timedelta
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import sqlite3

from ontology_review.publisher import current_release_id
from ontology_review.validators import prompt_for as ontology_prompt_for
from ontology_review.contracts import proposal_digest
from review_agent.repository import ReviewRepository
from review_agent.validator_contracts import prompt_for
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from review_agent.request_cache import validation_request_key

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev020_release_20261103'
TZ = timezone(timedelta(hours=8))
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
integrity = pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
stages = []
for name in ('initial', 'revision2', 'revision3'):
    folder = OUT if name == 'initial' else OUT / name
    p = json.loads((folder / 'frozen_proposal.json').read_text(encoding='utf-8'))
    assert proposal_digest(p) == p['proposal_digest']
    from review_agent.contracts import sha256_json
    assert sha256_json(ontology_prompt_for(p)) == p['versions']['prompt_hash']
    semantic = json.loads((folder / 'semantic_review.json').read_text(encoding='utf-8'))
    ontology = json.loads((folder / 'ontology_review.json').read_text(encoding='utf-8'))
    assert current_release_id(folder / 'ontology_releases') == p['base_release_id']
    assert not (folder / 'publication.json').exists()
    stages.append({'stage': name, 'extension_id': p['task_id'], 'delta_revision': p['object_revision'],
                   'proposal_digest': p['proposal_digest'], 'delta_hash': p['delta_hash'],
                   'semantic_seats': [{k: v.get(k) for k in ('voter_id', 'response_status', 'coverage', 'outcome_unknown')} for v in semantic['votes']],
                   'semantic_assessment': semantic['assessment'],
                   'ontology_seats': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'outcome_unknown')} for v in ontology['votes']],
                   'consensus': ontology['result']['consensus'], 'published': False,
                   'source_patch': {k: p['source_patch'][k] for k in ('before_triple_count', 'after_triple_count', 'preserved_triple_count', 'patch_hash')}})

latest = OUT / 'revision3'; repo = ReviewRepository(latest / 'development_review.sqlite3')
parent = json.loads((latest / 'frozen_proposal.json').read_text(encoding='utf-8'))
votes = json.loads((latest / 'ontology_review.json').read_text(encoding='utf-8'))['votes']
unknown = [v for v in votes if v.get('outcome_unknown')]; assert len(unknown) == 1
v = unknown[0]; history = repo.request_history(v['request_key']); assert len(history) == 1
attempt = history[0]; assert attempt['state'] == 'unknown'
reconciliation = {k: v.get(k) for k in ('voter_id', 'model_id', 'model_family', 'request_key', 'error_code', 'http_status', 'outcome_unknown')}
reconciliation.update(attempt_id=attempt['attempt_id'], provider_request_id=attempt.get('provider_request_id'),
                      started_at_beijing=datetime.fromtimestamp(attempt['started_at'], TZ).isoformat(),
                      deadline_at_beijing=datetime.fromtimestamp(attempt['deadline_at'], TZ).isoformat(),
                      task_id=parent['task_id'], delta_revision=parent['object_revision'],
                      parent_proposal_digest=parent['proposal_digest'], delta_hash=parent['delta_hash'],
                      actual_attempt_count=len(history), automatically_retried=False,
                      required_action='查询原Qwen服务日志/请求结果；按原请求对账。没有明确结果前不得另起同等长请求。')
(OUT / 'Qwen未知请求对账.json').write_text(json.dumps(reconciliation, ensure_ascii=False, indent=2), encoding='utf-8')
source4 = repo.get_revision('cand_52159eb442b708f4e860'); assert source4['revision'] == 4
decision = repo.latest_decision(source4['candidate_id']); assert decision['source_support_result'] == 'supported'
assert repo.get_candidate(source4['candidate_id'])['pending_extension_id'] == parent['task_id']

# Verify cached keys for the old v4 source method still reconstruct exactly.
method = json.loads((OUT / 'revision2/direction_scope_method_review.json').read_text(encoding='utf-8'))
old_audit = repo.get_audit(source4['candidate_id'], method['old_audit_id_preserved'])
old_source = old_audit['rounds'][-1]; roster = _configured_validators(load_config())
matched = 0
for voter in roster:
    generation = next((g for g in old_source.get('validator_generation', []) if g['voter_id'] == voter['id']), None)
    frozen_voter = {**voter, **({'max_output_tokens': generation['max_output_tokens']} if generation else {})}
    old_vote = next(v for v in old_audit['votes'] if v['round_id'] == old_source['round_id'] and v['voter_id'] == voter['id'])
    assert validation_request_key(frozen_voter, old_source) == old_vote['request_key']
    matched += 1
assert current_release_id(ROOT / 'runs/ontology_releases') == parent['base_release_id']
report = {'verified_at_beijing': datetime.now(TZ).isoformat(), 'stages': stages,
          'latest_candidate_revision': 4, 'latest_source_support': 'supported', 'latest_source_independent_votes': 3,
          'latest_ontology_approve_votes': 2, 'latest_ontology_unknown_votes': 1,
          'latest_semantic_valid_votes': 0, 'latest_semantic_contract_failures': 3,
          'reconciliation': reconciliation, 'historical_source_request_keys_restored': matched,
          'production_unchanged': integrity, 'formal_release_unchanged': parent['base_release_id'],
          'development_release_published': False, 'post_release_reaudit_executed': False,
          'next_steps': ['先对账Qwen未知请求，不重复发送', '恢复同一固定委员会的缺失本体票',
                         '在原请求/原证据下补正三席能力问题引文，并保留实质反对', '仅全部门槛通过后在开发分支发布、重新绑定并复审']}
(OUT / 'verification_and_summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# DEV-020 本体扩展继续处理结果', '',
         '当前尚未发布。最新候选已通过三席来源共识；最新本体草案两席批准，Qwen为HTTP 504执行结果未知；能力问题三席均因引文契约失败，尚无有效结论。未绕过固定三席和能力问题门槛，未重发未知请求。', '',
         '## 已完成', '',
         '- 将过泛的“具有特征”改为具体“章动角或攻角的定性大小”，值保留“很小”，稳定飞行条件与“总是”量词不变；在开发分支提交为候选第4版。',
         '- 对第4版执行完整来源审核。第一次一席把本体缺口混入原文方向判断，旧票保留；明确分层检查口径并升级提示版本后，用固定原三模型做完整对照，来源共识闭合。',
         '- 修正本体审核提示中domain/range与存在性约束的区分，保留旧提示快照；实际核对3张历史来源票的请求键与原值完全一致，避免新提示悄悄替换旧票。',
         '- 在开发草案中修复“空气—消耗—弹丸”的错误值域为“动能”；根据原文初始条件修正“初速”定义，补充“弹道高”定义。原文确实支持空气层带动外层空气和弹丸具有质量系数，因此保留这两类关系。',
         '- 能力核验仍交付完整章节和完整RDF；新草案增加经过坐标校验的正文定位片段，不附其他模型票或支持/反对角色。引文失败现在能定位到问题ID、引文序号和原文出现次数；格式补正仅向原模型提供自己的无效返回，不重投有效意见。', '',
         '## 三轮状态', '',
         '| 轮次 | 主要变化 | 本体审核 | 能力问题 |', '|---|---|---|---|',
         '| 原草案 | 通用“具有特征”字符串属性 | 2修订、1反对 | 未通过 |',
         '| 修订2 | 具体角度属性、修正消耗值域 | 3批准 | 一席有效反对、另有引文失败，未通过 |',
         '| 修订3（当前） | 补正基础定义、增加核验后的定位正文 | 2批准、Qwen结果未知 | 3席引文契约失败，0张有效能力票 |', '',
         '旧票全部保留，修订轮次对应不同的真实差异，不把旧批准绑定到新草案，也不把格式无效的原始意见计为通过。', '',
         '## 当前阻塞与恢复', '',
         f"Qwen原请求键：`{reconciliation['request_key']}`。返回Nginx 504，程序标记GATEWAY_REQUEST_OUTCOME_UNKNOWN，仅有1次实际尝试。对账信息见《Qwen未知请求对账.json》，不包含API密钥。", '',
         '需要先从原服务查明这次执行结果，再按原票据回填/恢复。现有能力核验代码要求父本体未知请求先对账，因此本轮没有绕开该栅栏继续重发。对账后，可在同一固定证据和委员会下补正能力核验引文；只有能力问题及三席本体审核全部通过，才开发发布并按新版本重新绑定、重新审核候选。', '',
         '## 范围', '',
         '所有候选、草案和模型请求均在独立开发数据库中。原人工Excel、30条生产候选绑定、生产320条候选状态、本体正式head和Neo4j均未改动。当前没有开发新版本发布，也没有发布后的复审结果；模型批准不能代替人工准确率或OWL逻辑一致性证明。', '',
         '代码检查及测试结果见regression_final.txt；本轮新增的正文校验和历史提示分派测试见对应tests文件。后续恢复须使用冻结提示版本，不能用当前提示直接重投历史未知请求。']
(OUT / 'DEV-020处理结果.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'source_supported': True, 'ontology_approve': 2, 'ontology_unknown': 1,
                  'semantic_contract_failures': 3, 'historical_request_keys_matched': matched,
                  'published': False, 'production_changed': False}, ensure_ascii=False))
