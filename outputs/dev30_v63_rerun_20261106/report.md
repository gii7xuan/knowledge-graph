# 30条开发样本新版重跑

更新时间：2026-11-06T09:53:01.746462+08:00

已开始 30/30，处理结束 30/30，三席独立审核齐全 17/30。

本次重跑原始冻结候选，不自动修改候选，不发布本体，不导入图谱。人工标注未提供给模型。
核心来源支持、完整表达支持、正式映射和知识收录范围分开统计。证据包已升级，旧人工标签仅作定性对照，不直接计算准确率。

|样本|三席齐全|核心来源|完整表达支持|表达|有效映射|知识范围|流程|
|---|---|---|---|---|---|---|---|
|DEV-001|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-002|是|supported|supported|faithful|unassigned|unresolved|mapping_review|
|DEV-003|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-004|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-005|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-006|否|unresolved|unresolved|needs_revision|unassigned|unresolved|manual_review|
|DEV-007|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-008|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-009|是|supported|supported|faithful|unassigned|unresolved|mapping_review|
|DEV-010|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-011|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-012|是|supported|supported|faithful|unassigned|conditional|mapping_review|
|DEV-013|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-014|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-015|否|unresolved|unresolved|unresolved|unassigned|unresolved|manual_review|
|DEV-016|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-017|是|supported|supported|faithful|unassigned|unresolved|mapping_review|
|DEV-018|是|supported|unresolved|needs_revision|unassigned|general|revision_required|
|DEV-019|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-020|是|supported|supported|faithful|unassigned|unresolved|mapping_review|
|DEV-021|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-022|否|unresolved|unresolved|needs_revision|unassigned|unresolved|manual_review|
|DEV-023|是|supported|supported|faithful|unassigned|conditional|mapping_review|
|DEV-024|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-025|是|supported|supported|faithful|unassigned|general|mapping_review|
|DEV-026|否|unresolved|unresolved|needs_revision|unassigned|unresolved|revision_required|
|DEV-027|是|supported|supported|faithful|unassigned|conditional|mapping_review|
|DEV-028|否|unresolved|unresolved|unresolved|constraint_conflict|unresolved|manual_review|
|DEV-029|是|supported|supported|faithful|unassigned|local_definition|excluded_by_scope|
|DEV-030|是|supported|supported|faithful|unassigned|unresolved|mapping_review|

原工作簿和生产库中这30条候选的哈希已核对，均未改动。
完整逐项意见见 comparison.json，冻结审核证据包见 review_packets.json。
旧V5/V5.1/V6.2实验继续保留；本次采用V6.3动态本体上下文和独立数据库，旧票不计入新三席，未改写历史未知请求。

请求状态及已知token用量：
```json
{
  "request_attempts": 93,
  "attempt_state_counts": {
    "completed": 78,
    "unknown": 13,
    "failed": 2
  },
  "usage": {
    "input_tokens": 983899,
    "output_tokens": 207070,
    "unknown_usage_attempts": 13
  },
  "planner_usage": {
    "input_tokens": 1469022,
    "output_tokens": 29467
  },
  "field_corrections": {
    "reserved_or_attempted": 0,
    "states": {},
    "reserved_tokens": 0,
    "known_actual_tokens": 0
  }
}
```
