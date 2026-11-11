# 原24条人工任务的只读分流

以原人工任务为固定总体。只替换同提案、同版本的已完成晚到合格票，不补造席位、不清除历史问题、不改写正式决定。多种阻塞可以并存；主队列只指定先处理什么。

|样本/版本|原合格席位→晚到后|晚到票|首要队列|派生建议|
|---|---|---|---|---|
|DEV-001 / v1|3→3|无|证据角色/维度引用核对|manual_review|
|DEV-002 / v3|2→2|无|缺少合格席位|manual_review|
|DEV-005 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-006 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-009 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-010 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-011 / v2|3→3|无|具体含义或范围争议|manual_review|
|DEV-012 / v2|3→3|无|证据角色/维度引用核对|manual_review|
|DEV-013 / v1|3→3|无|证据角色/维度引用核对|manual_review|
|DEV-015 / v2|3→3|无|证据角色/维度引用核对|manual_review|
|DEV-016 / v2|2→2|无|缺少合格席位|manual_review|
|DEV-017 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-018 / v1|2→3|judge_b|证据角色/维度引用核对|manual_review|
|DEV-019 / v1|0→0|无|缺少合格席位|manual_review|
|DEV-020 / v2|2→2|无|缺少合格席位|manual_review|
|DEV-021 / v3|2→2|无|缺少合格席位|manual_review|
|DEV-022 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-023 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-024 / v2|3→3|无|具体含义或范围争议|manual_review|
|DEV-025 / v2|3→3|无|具体含义或范围争议|manual_review|
|DEV-026 / v1|3→3|无|具体含义或范围争议|manual_review|
|DEV-027 / v1|2→2|无|缺少合格席位|manual_review|
|DEV-028 / v1|2→3|judge_b|正式映射待处理|wait_mapping|
|DEV-030 / v1|2→3|judge_b|证据角色/维度引用核对|manual_review|

## 证据角色/维度引用核对（6条）

核对正面证据集合、证据角色和维度引用；不是增加一个新审核席位

- DEV-001：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`。
- DEV-012：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`。
- DEV-013：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`、`unresolved_claim_dispute`。
- DEV-015：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`、`unresolved_claim_dispute`。
- DEV-018：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_expression_incomplete`、`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`。
- DEV-030：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`。

## 缺少合格席位（13条）

补齐已失败席位的实际契约问题；晚到合格结果优先，不重审已有合格席位

- DEV-002：source=unresolved（有效2/3，分歧True）；expression=unresolved（有效2/3，分歧True）；mapping=context_incomplete（有效3/3，分歧True）；scope=include（有效3/3，分歧False）。
  并存阻塞：`critical_issue_unresolved`、`expression:unresolved`、`gap:ontology_disagreement`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`source:unresolved`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。
- DEV-005：source=unresolved（有效2/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=unclear（有效3/3，分歧True）。
  并存阻塞：`candidate_expression_incomplete`、`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`、`unresolved_dimension_disagreement`。
- DEV-006：source=unresolved（有效1/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_expression_incomplete`、`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`source:unresolved`。
- DEV-009：source=unresolved（有效1/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=unclear（有效3/3，分歧True）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`、`unresolved_dimension_disagreement`。
- DEV-010：source=unresolved（有效2/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`。
- DEV-016：source=unresolved（有效1/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`source:unresolved`。
- DEV-017：source=unresolved（有效2/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`source:unresolved`。
- DEV-019：source=unresolved（有效0/3，分歧False）；expression=unresolved（有效0/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`。
- DEV-020：source=unresolved（有效1/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`、`unresolved_claim_dispute`。
- DEV-021：source=unresolved（有效2/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`。
- DEV-022：source=unresolved（有效2/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=unclear（有效3/3，分歧True）。
  并存阻塞：`candidate_expression_incomplete`、`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。
- DEV-023：source=unresolved（有效1/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`。
- DEV-027：source=unresolved（有效1/3，分歧False）；expression=unresolved（有效2/3，分歧False）；mapping=context_incomplete（有效2/3，分歧False）；scope=unclear（有效2/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`required_committee_incomplete`、`scope:unclear`、`source:unresolved`。

## 具体含义或范围争议（4条）

仅复核 source,expression 的具体争议及对应原文，保留反对意见

- DEV-011：source=unresolved（有效2/3，分歧True）；expression=unresolved（有效3/3，分歧True）；mapping=context_incomplete（有效3/3，分歧False）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`gap:need_more_evidence`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。
- DEV-024：source=unresolved（有效2/3，分歧True）；expression=unresolved（有效3/3，分歧True）；mapping=context_incomplete（有效3/3，分歧True）；scope=include（有效3/3，分歧False）。
  并存阻塞：`critical_issue_unresolved`、`expression:unresolved`、`formal_mapping_not_bound_to_candidate`、`gap:ontology_disagreement`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。
- DEV-025：source=unresolved（有效3/3，分歧True）；expression=unresolved（有效3/3，分歧True）；mapping=context_incomplete（有效3/3，分歧True）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`expression:unresolved`、`gap:ontology_disagreement`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`source:unresolved`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。
- DEV-026：source=unresolved（有效2/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧False）；scope=unclear（有效3/3，分歧True）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`scope:unclear`、`source:unresolved`、`unresolved_dimension_disagreement`。

## 正式映射待处理（1条）

补充绑定版本的关系定义/身份/约束，保持事实表达

- DEV-028：source=supported（有效3/3，分歧False）；expression=standard（有效3/3，分歧False）；mapping=context_incomplete（有效3/3，分歧True）；scope=include（有效3/3，分歧False）。
  并存阻塞：`candidate_identity_unresolved`、`critical_issue_unresolved`、`formal_mapping_not_bound_to_candidate`、`gap:ontology_disagreement`、`mapping:context_incomplete`、`ontology_guard_not_passed`、`unresolved_claim_dispute`、`unresolved_dimension_disagreement`。

DEV-028虽然派生建议为wait_mapping，仍有映射分歧与历史关键问题，不能视为已获准入。
修订队列当前没有已确认、可直接执行的条目；需先解决缺票/争议并形成具体方案，再按新版本重新验证。
逐条proposal_digest、evidence_hash、晚到receipt_hash及保留问题见同名JSON。
