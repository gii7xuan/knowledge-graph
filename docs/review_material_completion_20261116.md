# 限定信息、本体材料与请求未知处理

本轮在已有动态权重实现上继续，起始仓库commit仍为`016347875f01cc5f0f9f358f8ec48ce3b0d35221`。全部真实处理位于`outputs/review_completion_20261116/live`，原候选库及正式本体发布未修改。

## 真实候选限定补全

对象为调试样本DEV-006、候选`cand_de3414495fa05d4e6672`。唯一未知的限定字段为`polarity`；条件、时间、适用范围、数量的`not_stated`保持不变。

1. Agent根据原文及上下文提出`polarity=positive`、`qualifier_status.polarity=extracted`，程序禁止编辑其他限定、本体ID、实体和谓词。
2. 首次规划响应的说明超过400字符。保留完整原始响应，只对说明字段做有哈希和路径记录的长度投影，不改字段值或引用，不重调规划模型。
3. 首次三席修订审核均输出approve，但未完整填入已检查材料ID，仍按契约失败保留。已有恢复流程向这三笔已知失败补充明确的字段说明，三席补正后全部有效通过；未重试任何结果未知请求。
4. 在隔离库应用为版本2，并取得新的三席独立审核；旧票不复用。新版本source=supported、expression=standard、scope=include；mapping=context_incomplete，整体继续manual_review。

本轮新增10个真实请求：1次规划、3次首次修订审核、3次定向契约补正、3次新版本审核。共6个completed、4个已知failed，无新增unknown。前次失败、格式投影及恢复调用均保留并计费记录，不能只计算成功6次。整体调用上限由初始8明确调整为10，对应追加预算事件保存在隔离库。

证据：

- `contract_recovery_result.json`：最终结果与全部新调用。
- `contract_recovery_completed_candidate.json`：版本2。
- `contract_recovery_revision_review.json`：三席有效修订批准。
- `contract_recovery_new_version_review.json`：版本2独立审核。
- `continued_qualifier_proposal.json`：原始规划响应和说明长度投影证明。
- 各阶段`execution_code.zip`：实际执行版本；后续提示词澄清及传输元数据改进单独经过回归。

限定字段已补齐不等于整条候选可以入图；本轮没有人工金标签，准确率和修订质量仍不能作为论文结果。

## 本体映射材料

定位到确定的导出缺陷：`runs/generate_chapter1_ontology.py`原先为类写入`skos:definition`，却没有为对象属性写入已有`definition_candidate`。上游候选保留了定义，正式Turtle因漏写而缺失。

修复后，按属性IRI、上游candidate_id、关系名称、domain/range及逐字原文位置，恢复81条关系定义材料，0条来源冲突或无法恢复。生成的Turtle和JSON草案保持原属性ID、关系名称和端点约束；没有用关系标签凭空生成定义，也没有覆盖原发布版本。

材料文件：

- `definition_materials.json`：81条定义、原候选哈希、原文区间和来源证据。
- `definition_restoration_draft.ttl`、`definition_restoration_draft.json`：恢复注释的待审本体草案。
- `definition_review_template.json`：逐条独立审核空表；它是共享本体注释审核，不是候选事实金标签。
- `mapping_materials.json`：完整既有关系清单、当前候选证据和剩余建模问题。

当前候选的“类似于”没有精确名称或声明别名匹配；尾部“弯曲壁面边界层出现分离现象”的指称和实体/事件/命题建模也尚未形成正式映射。不能将它强配为“形成”“引起”或“近似看成”。本轮准备了定义恢复及缺口核验材料，未将未审核的注释或新术语伪装成已批准本体。

## 旧504与后续请求

旧请求`attempt_c960687b555f46418bb0c61021881553`，北京时间2026-11-16 16:09:26发起，模型Qwen，网关504。没有服务端请求ID和用量；用户确认没有网关日志入口。因此无法证实提供商是否执行或如何计费，原状态和未知费用继续保留。

它只产生规划输出，未执行对应工具，且版本2已经独立复核，所以不作为新版本的语义依赖；旧动作禁止晚到执行，也不自动重发。`unknown_request_disposition.json`记录用户无日志和隔离处置。不能把该请求改为not_sent、完成或零费用。

新增请求使用持久化attempt_id作为`X-Client-Request-Id`发送，并保存客户端关联ID、服务端关联ID/响应ID与传输诊断；修订审核也保留成功及失败响应的这些信息。补全规划使用精简材料和流式输出，减少大段历史上下文造成的等待。当前10次新请求全部有明确终态。

## 复现与验证

在仓库PowerShell中使用：

```powershell
$completionPython = 'D:\miniconda3\envs\knowledge\python.exe'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $completionPython -B -m evaluation.complete_review_materials --output outputs/new_completion_materials
```

不带`--run`仅生成材料；新目录加`--run`才执行真实补全。恢复模式只允许已有候选未变的同一隔离目录：`--continue-format`读取已知说明超长响应，`--recover-known-contract`只修复已知材料清单契约失败。每个阶段有独占启动标记，不能自动重复启动。

```powershell
& $completionPython -B scripts/run_weighting_checks.py tests.test_review_completion tests.test_transport_diagnostics tests.test_revision_source_obligation tests.test_revision_mapping_context tests.test_revision_evidence_handoff tests.test_revision_loop tests.test_dev_semantic_revision tests.test_resolution_workflows tests.test_vote_weighting tests.test_agent_resolution --log outputs/review_completion_20261116/reproduced_checks.log
```

检查覆盖未知字段限定、保留not_stated、合法弃权、只投影说明文本、独立修订批准及新版本复核、低权重反对阻断、定义恢复身份不变、来源冲突不自动合并、请求关联ID、504隔离与不重发、截断和坏JSON不成为批准。

最终113项检查全部通过，无跳过，日志为`outputs/review_completion_20261116/verified_checks.log`。检查期间发现并修复了旧版本提示词兼容问题：材料字段澄清仅适用于`full_expression_v2`，旧版本提示词保持原解释。`git diff --check`通过。

后续应先独立审核恢复的共享定义，再建立供各实验组共同使用的新冻结本体；当前类比关系的语义覆盖和尾部建模仍需单独判定。旧504的账目保持明确缺失，不阻塞受控新实验。
