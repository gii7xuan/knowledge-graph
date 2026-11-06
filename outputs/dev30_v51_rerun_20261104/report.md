# 30条开发样本新版重跑

更新时间：2026-11-04T18:00:09.509913+08:00

已开始 30/30，处理结束 30/30，三席独立审核齐全 0/30。

本次重跑原始冻结候选，不自动修改候选，不发布本体，不导入图谱。人工标注未提供给模型。
核心来源支持、完整表达支持、正式映射和知识收录范围分开统计。证据包已升级，旧人工标签仅作定性对照，不直接计算准确率。

|样本|三席齐全|核心来源|完整表达支持|表达|有效映射|知识范围|流程|
|---|---|---|---|---|---|---|---|
|DEV-001|否|—|—|—|—|—|—|
|DEV-002|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-003|否|—|—|—|—|—|—|
|DEV-004|否|—|—|—|—|—|—|
|DEV-005|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-006|否|—|—|—|—|—|—|
|DEV-007|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-008|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-009|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-010|否|—|—|—|—|—|—|
|DEV-011|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-012|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-013|否|—|—|—|—|—|—|
|DEV-014|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-015|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-016|否|—|—|—|—|—|—|
|DEV-017|否|—|—|—|—|—|—|
|DEV-018|否|—|—|—|—|—|—|
|DEV-019|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-020|否|—|—|—|—|—|—|
|DEV-021|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-022|否|—|—|—|—|—|—|
|DEV-023|否|—|—|—|—|—|—|
|DEV-024|否|—|—|—|—|—|—|
|DEV-025|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-026|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-027|否|—|—|—|—|—|—|
|DEV-028|否|unresolved|unresolved|unresolved|constraint_conflict|unresolved|revision_required|
|DEV-029|否|—|—|—|—|—|—|
|DEV-030|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|

原工作簿和生产库中这30条候选的哈希已核对，均未改动。
完整逐项意见见 comparison.json，冻结审核证据包见 review_packets.json。
旧V5试跑单独留存在 dev30_v5_rerun_20261104；其中的结果未知请求未被重发，也不计入本次V5.1三席。

请求状态及已知token用量：
```json
{
  "request_attempts": 182,
  "attempt_state_counts": {
    "failed": 151,
    "completed": 25,
    "unknown": 6
  },
  "usage": {
    "input_tokens": 9196585,
    "output_tokens": 445289,
    "unknown_usage_attempts": 6
  },
  "planner_usage": {
    "input_tokens": 2091184,
    "output_tokens": 23948
  }
}
```
