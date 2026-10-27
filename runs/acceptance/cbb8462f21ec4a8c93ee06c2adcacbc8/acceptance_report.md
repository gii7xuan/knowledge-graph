# T01～T40 验收核对与补漏

运行时间：2026-10-27T02:51:09.722219+00:00。结果：40/40 项通过。

验收对象为工程行为与控制流。模型响应为 Mock；图谱场景使用隔离的真实 Neo4j，发布竞争包含独立进程事务。此报告不代表真实模型准确率。

| 编号 | 场景 | 结果 | 验证要求 |
| --- | --- | --- | --- |
| T01 | 初始本体仅生成候选，未发布 | passed | 构建成功不冒充发布，不使用旧本体继续抽取 |
| T02 | OWL domain 与 allowed_any 不同 | passed | 合取语义显式处理，未执行公理阻止自动使用 |
| T03 | 数据属性 range 为 XSD | passed | 保留数据类型并校验词法，不创建悬空类 |
| T04 | label 与标准关系 ID | passed | 批准别名、label 和 ID 映射后同身份，并实际入图 |
| T05 | 相同头尾，不同条件/极性/模态 | passed | 完整限定决定独立事实键 |
| T06 | 漏填极性 | passed | 保持 unknown，不默认 positive |
| T07 | 两来源抽出同一事实 | passed | 保留独立候选及证据锚点，审核后聚合一个 Fact |
| T08 | 子本体没有，完整本体已有 | passed | 查询完整本体并映射，阻止重复扩展 |
| T09 | 原文支持，本体缺失 | passed | 原文共识成立并进入扩展等待 |
| T10 | 三元组 uncertain 仅因 Schema 缺口 | passed | 保留来源支持，不判事实假 |
| T11 | 原文不足 | passed | 缺少独立来源支持不能创建扩展请求 |
| T12 | 原文支持，怀疑既有约束错误 | passed | 保留 guard 原失败并进入约束复核 |
| T13 | 一实例泛化为通用约束 | passed | 必须引用定义/分类/范围依据，实例证据不能批准 |
| T14 | 同一扩展由多个候选触发 | passed | 去重扩展任务，保留独立订阅及成本 |
| T15 | 两个提案都基于 V1，先后发布 | passed | 旧 base CAS 失败，必须 rebase 和重新审核 |
| T16 | delta 变化沿用旧票 | passed | 摘要和差异修订不匹配，拒绝发布 |
| T17 | 发布重复通知与候选已变更 | passed | 幂等唤醒，不复用旧票、不重启过期订阅 |
| T18 | 扩展被人工拒绝 | passed | 候选进入 schema_unresolved，来源支持和真值标签保留 |
| T19 | domain/range 或祖先语义变化影响历史候选 | passed | 列出传递影响，创建新修订和重审，撤回旧图贡献 |
| T20 | 人工提交空、假、跨候选和过期证据 | passed | 无正式贡献；非法证据 422，并发版本冲突 409 |
| T21 | 人工选择合法新原文 | passed | 服务端从规范全文核验，绑定新人工证据包后允许裁决 |
| T22 | 正确候选首轮缺上下文 | passed | 真实补证和复核后解除证据不足 |
| T23 | 候选本身漏条件 | passed | 旧表达不能靠补证通过，须新修订 |
| T24 | 规划器声称反证已解决 | passed | 文本声明和同一证据不能清除义务 |
| T25 | 首轮反证，次轮支持 | passed | 精确选定轮次和提案，拒绝内容篡改和历史混票 |
| T26 | 三席一超时、两 valid | passed | 固定三席分母，不能形成正式共识 |
| T27 | 同一模型不同别名 | passed | 实际身份去重，不增加独立票 |
| T28 | 三元组票用于本体发布 | passed | 拒绝跨任务、契约和版本票据重放 |
| T29 | 同名标题、英文多句、跨块条件 | passed | 章节位置、句界、上下文保持正确 |
| T30 | 保留版本号，篡改正文或偏移 | passed | 摘要与结构验证失败 |
| T31 | revision1 入库→修改2→快速再通过2 | passed | 停用旧贡献，清理旧 Fact，只有新修订有效 |
| T32 | 两候选支持同 Fact，撤回一条 | passed | 另一贡献仍有效 |
| T33 | outbox 乱序/重复/提交后崩溃 | passed | 幂等收敛，旧 worker 不能回退新版本 |
| T34 | 否定、条件、能力与其他限定入图 | passed | 完整 Fact 和 typed literal，不产生无条件肯定边 |
| T35 | 多候选并发更新同 Fact | passed | 按锁顺序更新和重算全部有效贡献 |
| T36 | 简单多数 2 valid、2 invalid、1 uncertain | passed | 任何输入排列均 unresolved |
| T37 | 超时、失败、标签缺失 | passed | 冻结全集保留缺失结果，覆盖率用全集、准确性用标注子集 |
| T38 | shadow 建议通过或批准扩展 | passed | 不自动正式入库或发布 |
| T39 | 扩展—重审反复触发 | passed | 同一 case 总预算跨 Agent 与新 revision 保留，耗尽受控停止 |
| T40 | 合法初速/射程与正文假设句 | passed | 不因短词或假设二字删除 |

## 可复查的代码与测试

### T01：初始本体仅生成候选，未发布

构建成功不冒充发布，不使用旧本体继续抽取

代码：[ontologyBuild/release_service.py](../../ontologyBuild/release_service.py), [scripts/run_ontology_then_extract.py](../../scripts/run_ontology_then_extract.py)

- `passed` [tests.test_ontology_import.OntologyImportTests.test_import_alone_has_no_release_and_tools_are_not_claimed_run](../../tests/test_ontology_import.py#L93)（0.234 秒）
- `passed` [tests.test_ontology_import.OntologyImportTests.test_successful_legacy_generation_never_extracts_with_an_old_ontology](../../tests/test_ontology_import.py#L204)（0.016 秒）

### T02：OWL domain 与 allowed_any 不同

合取语义显式处理，未执行公理阻止自动使用

代码：[ontologyBuild/owl_to_project_schema.py](../../ontologyBuild/owl_to_project_schema.py), [review_agent/ontology_guard.py](../../review_agent/ontology_guard.py)

- `passed` [tests.test_ontology_import.OntologyImportTests.test_multiple_domains_are_conjunctive_and_all_parents_survive](../../tests/test_ontology_import.py#L45)（0.015 秒）
- `passed` [tests.test_ontology_import.OntologyImportTests.test_unexecuted_restriction_is_preserved_and_blocks_auto_review](../../tests/test_ontology_import.py#L69)（0.016 秒）

### T03：数据属性 range 为 XSD

保留数据类型并校验词法，不创建悬空类

代码：[ontologyBuild/owl_to_project_schema.py](../../ontologyBuild/owl_to_project_schema.py), [review_agent/ontology_guard.py](../../review_agent/ontology_guard.py)

- `passed` [tests.test_ontology_import.OntologyImportTests.test_xsd_range_is_a_datatype_and_lexical_values_are_checked](../../tests/test_ontology_import.py#L53)（0.016 秒）

### T04：label 与标准关系 ID

批准别名、label 和 ID 映射后同身份，并实际入图

代码：[schema/relation_registry.py](../../schema/relation_registry.py), [to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_t04_label_alias_and_id_share_one_imported_fact](../../tests/test_admission_graph_sync.py#L224)（6.953 秒）

### T05：相同头尾，不同条件/极性/模态

完整限定决定独立事实键

代码：[review_agent/contracts.py](../../review_agent/contracts.py), [schema/fact_identity.py](../../schema/fact_identity.py)

- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_qualifiers_and_typed_literal_identity_are_not_collapsed](../../tests/test_source_candidate_v4.py#L168)（0.0 秒）

### T06：漏填极性

保持 unknown，不默认 positive

代码：[extract_triplets/candidate_contract.py](../../extract_triplets/candidate_contract.py)

- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_missing_polarity_and_status_remain_unknown](../../tests/test_source_candidate_v4.py#L149)（0.0 秒）

### T07：两来源抽出同一事实

保留独立候选及证据锚点，审核后聚合一个 Fact

代码：[extract_triplets/candidate_contract.py](../../extract_triplets/candidate_contract.py), [to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_t07_distinct_source_spans_preserved_in_one_fact](../../tests/test_admission_graph_sync.py#L235)（1.11 秒）

### T08：子本体没有，完整本体已有

查询完整本体并映射，阻止重复扩展

代码：[review_agent/tools.py](../../review_agent/tools.py), [schema/relation_registry.py](../../schema/relation_registry.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_nullable_full_lookup_finds_existing_mapping_and_blocks_duplicate](../../tests/test_review_enhancement_v4.py#L165)（1.64 秒）

### T09：原文支持，本体缺失

原文共识成立并进入扩展等待

代码：[review_agent/consensus.py](../../review_agent/consensus.py), [review_agent/lifecycle_repository.py](../../review_agent/lifecycle_repository.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_source_supported_schema_gap_creates_waiting_task_without_outbox](../../tests/test_review_enhancement_v4.py#L124)（1.25 秒）

### T10：三元组 uncertain 仅因 Schema 缺口

保留来源支持，不判事实假

代码：[review_agent/response_contract.py](../../review_agent/response_contract.py), [review_agent/consensus.py](../../review_agent/consensus.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_source_supported_schema_gap_creates_waiting_task_without_outbox](../../tests/test_review_enhancement_v4.py#L124)（1.25 秒）
- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_schema_only_failure_cannot_claim_source_rejection](../../tests/test_review_enhancement_v4.py#L385)（0.094 秒）

### T11：原文不足

缺少独立来源支持不能创建扩展请求

代码：[review_agent/tools.py](../../review_agent/tools.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t11_insufficient_source_cannot_trigger_schema_expansion](../../tests/test_acceptance_gaps.py#L97)（1.375 秒）

### T12：原文支持，怀疑既有约束错误

保留 guard 原失败并进入约束复核

代码：[review_agent/ontology_guard.py](../../review_agent/ontology_guard.py), [review_agent/tools.py](../../review_agent/tools.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_constraint_review_retains_guard_failure_and_never_approves_ontology](../../tests/test_review_enhancement_v4.py#L260)（1.141 秒）

### T13：一实例泛化为通用约束

必须引用定义/分类/范围依据，实例证据不能批准

代码：[ontology_review/contracts.py](../../ontology_review/contracts.py), [ontology_review/validators.py](../../ontology_review/validators.py), [prompts/ontology_extension_validator_v1.txt](../../prompts/ontology_extension_validator_v1.txt)

- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t13_instance_only_cannot_approve_general_constraints](../../tests/test_acceptance_gaps.py#L154)（3.781 秒）
- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t13_definition_grounding_is_required_and_can_pass](../../tests/test_acceptance_gaps.py#L168)（3.625 秒）

### T14：同一扩展由多个候选触发

去重扩展任务，保留独立订阅及成本

代码：[review_agent/lifecycle_repository.py](../../review_agent/lifecycle_repository.py), [review_agent/case_budget.py](../../review_agent/case_budget.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_shared_task_has_two_subscriptions_and_charges_both_cases](../../tests/test_ontology_extension_agent.py#L275)（6.25 秒）

### T15：两个提案都基于 V1，先后发布

旧 base CAS 失败，必须 rebase 和重新审核

代码：[ontologyBuild/release_catalog.py](../../ontologyBuild/release_catalog.py), [ontology_review/publisher.py](../../ontology_review/publisher.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_release_head_race_requires_rebase_and_fresh_review](../../tests/test_ontology_extension_agent.py#L221)（7.609 秒）
- `passed` [tests.test_ontology_release.OntologyReleaseTests.test_independent_processes_cannot_both_publish_against_the_same_base](../../tests/test_ontology_release.py#L77)（0.672 秒）

### T16：delta 变化沿用旧票

摘要和差异修订不匹配，拒绝发布

代码：[ontology_review/contracts.py](../../ontology_review/contracts.py), [ontology_review/publisher.py](../../ontology_review/publisher.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_new_delta_invalidates_old_round_and_stale_approval](../../tests/test_ontology_extension_agent.py#L192)（4.297 秒）

### T17：发布重复通知与候选已变更

幂等唤醒，不复用旧票、不重启过期订阅

代码：[ontology_review/publisher.py](../../ontology_review/publisher.py), [review_agent/repository.py](../../review_agent/repository.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_publication_is_idempotent_and_never_rebinds_twice](../../tests/test_ontology_extension_agent.py#L183)（4.578 秒）
- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_rebound_candidate_uses_new_proposal_and_never_old_votes](../../tests/test_ontology_extension_agent.py#L317)（5.25 秒）
- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_revocation_between_release_commit_and_wakeup_keeps_human_change](../../tests/test_ontology_extension_agent.py#L528)（4.719 秒）

### T18：扩展被人工拒绝

候选进入 schema_unresolved，来源支持和真值标签保留

代码：[ontology_review/repository.py](../../ontology_review/repository.py), [ontology_review/service.py](../../ontology_review/service.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t18_operator_rejection_updates_subscribers_without_false_truth_label](../../tests/test_acceptance_gaps.py#L141)（1.672 秒）

### T19：domain/range 或祖先语义变化影响历史候选

列出传递影响，创建新修订和重审，撤回旧图贡献

代码：[ontology_review/impact.py](../../ontology_review/impact.py), [ontology_review/publisher.py](../../ontology_review/publisher.py), [to_neo4j/reconciler.py](../../to_neo4j/reconciler.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_t19_schema_impact_withdraws_previously_projected_history](../../tests/test_admission_graph_sync.py#L209)（6.734 秒）
- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t19_domain_range_change_reaudits_previously_accepted_history](../../tests/test_acceptance_gaps.py#L229)（5.813 秒）
- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t19_indirect_historical_impact_creates_revision_and_reaudit](../../tests/test_acceptance_gaps.py#L185)（5.515 秒）

### T20：人工提交空、假、跨候选和过期证据

无正式贡献；非法证据 422，并发版本冲突 409

代码：[review_agent/human_evidence.py](../../review_agent/human_evidence.py), [review_agent/admission.py](../../review_agent/admission.py), [workbench_app.py](../../workbench_app.py)

- `passed` [tests.test_admission_graph_sync.AdmissionTests.test_nonempty_exact_human_evidence_required](../../tests/test_admission_graph_sync.py#L78)（0.563 秒）
- `passed` [tests.test_admission_graph_sync.AdmissionTests.test_bundle_stale_and_foreign](../../tests/test_admission_graph_sync.py#L93)（0.453 秒）
- `passed` [tests.test_admission_graph_sync.AdmissionTests.test_http_evidence_errors_and_conflicts](../../tests/test_admission_graph_sync.py#L164)（1.203 秒）

### T21：人工选择合法新原文

服务端从规范全文核验，绑定新人工证据包后允许裁决

代码：[review_agent/human_evidence.py](../../review_agent/human_evidence.py), [review_agent/admission_repository.py](../../review_agent/admission_repository.py)

- `passed` [tests.test_admission_graph_sync.AdmissionTests.test_bound_acceptance_and_idempotency](../../tests/test_admission_graph_sync.py#L85)（0.5 秒）
- `passed` [tests.test_admission_graph_sync.AdmissionTests.test_http_evidence_errors_and_conflicts](../../tests/test_admission_graph_sync.py#L164)（1.203 秒）

### T22：正确候选首轮缺上下文

真实补证和复核后解除证据不足

代码：[review_agent/issue_ledger.py](../../review_agent/issue_ledger.py), [review_agent/runtime.py](../../review_agent/runtime.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_actual_react_rechecks_new_evidence_and_clears_only_insufficiency](../../tests/test_review_enhancement_v4.py#L190)（2.875 秒）

### T23：候选本身漏条件

旧表达不能靠补证通过，须新修订

代码：[review_agent/issue_ledger.py](../../review_agent/issue_ledger.py), [review_agent/action_policy.py](../../review_agent/action_policy.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_condition_error_cannot_be_washed_away_by_new_support](../../tests/test_review_enhancement_v4.py#L209)（2.625 秒）
- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_new_audit_does_not_erase_a_revision_bound_condition_issue](../../tests/test_review_enhancement_v4.py#L321)（3.281 秒）

### T24：规划器声称反证已解决

文本声明和同一证据不能清除义务

代码：[review_agent/issue_ledger.py](../../review_agent/issue_ledger.py)

- `passed` [tests.test_review_enhancement_v4.IssueLifecycleTests.test_same_evidence_and_planner_text_cannot_resolve](../../tests/test_review_enhancement_v4.py#L49)（0.0 秒）
- `passed` [tests.test_review_enhancement_v4.IssueLifecycleTests.test_all_protected_references_must_be_independently_rechecked](../../tests/test_review_enhancement_v4.py#L59)（0.0 秒）

### T25：首轮反证，次轮支持

精确选定轮次和提案，拒绝内容篡改和历史混票

代码：[review_agent/proposal_resolver.py](../../review_agent/proposal_resolver.py), [review_agent/consensus.py](../../review_agent/consensus.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_conflict_is_resolved_by_current_independent_checks_and_import_uses_only_selected_round](../../tests/test_review_enhancement_v4.py#L226)（2.828 秒）
- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_exact_selection_detects_changed_evidence_and_ignores_other_rounds](../../tests/test_review_enhancement_v4.py#L345)（1.75 秒）

### T26：三席一超时、两 valid

固定三席分母，不能形成正式共识

代码：[review_agent/consensus.py](../../review_agent/consensus.py), [review_agent/validators.py](../../review_agent/validators.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t26_three_seats_one_timeout_do_not_form_triple_consensus](../../tests/test_acceptance_gaps.py#L123)（0.579 秒）

### T27：同一模型不同别名

实际身份去重，不增加独立票

代码：[review_agent/consensus.py](../../review_agent/consensus.py)

- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_reported_same_actual_model_does_not_get_distinct_seats](../../tests/test_review_enhancement_v4.py#L290)（1.578 秒）
- `passed` [tests.test_review_enhancement_v4.EnhancedAgentTests.test_duplicate_model_aliases_do_not_form_independent_source_consensus](../../tests/test_review_enhancement_v4.py#L367)（1.578 秒）

### T28：三元组票用于本体发布

拒绝跨任务、契约和版本票据重放

代码：[ontology_review/validators.py](../../ontology_review/validators.py), [ontology_review/contracts.py](../../ontology_review/contracts.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_triple_ticket_cannot_be_used_as_ontology_approval](../../tests/test_ontology_extension_agent.py#L266)（5.297 秒）

### T29：同名标题、英文多句、跨块条件

章节位置、句界、上下文保持正确

代码：[document/hierarchy_index.py](../../document/hierarchy_index.py), [document/snapshot.py](../../document/snapshot.py)

- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_identical_repeated_headings_have_distinct_positions](../../tests/test_source_candidate_v4.py#L87)（0.0 秒）
- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_english_decimal_abbreviation_and_fenced_heading_boundaries](../../tests/test_source_candidate_v4.py#L96)（0.0 秒）
- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_context_crosses_chunk_boundaries_without_crossing_sections](../../tests/test_source_candidate_v4.py#L118)（0.015 秒）

### T30：保留版本号，篡改正文或偏移

摘要与结构验证失败

代码：[document/index_integrity.py](../../document/index_integrity.py), [evidence/verification.py](../../evidence/verification.py)

- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_tampering_text_offsets_or_structure_fails_without_version_change](../../tests/test_source_candidate_v4.py#L129)（0.032 秒）

### T31：revision1 入库→修改2→快速再通过2

停用旧贡献，清理旧 Fact，只有新修订有效

代码：[review_agent/admission_repository.py](../../review_agent/admission_repository.py), [to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_changed_fact_retires_old_fact_even_with_stale_retract](../../tests/test_admission_graph_sync.py#L281)（1.578 秒）

### T32：两候选支持同 Fact，撤回一条

另一贡献仍有效

代码：[to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_shared_fact_withdrawal_and_concurrency](../../tests/test_admission_graph_sync.py#L305)（1.593 秒）

### T33：outbox 乱序/重复/提交后崩溃

幂等收敛，旧 worker 不能回退新版本

代码：[to_neo4j/reconciler.py](../../to_neo4j/reconciler.py), [to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_duplicate_reorder_and_old_worker](../../tests/test_admission_graph_sync.py#L266)（1.25 秒）
- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_atomic_rollback_and_crash_retry](../../tests/test_admission_graph_sync.py#L334)（0.954 秒）
- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_periodic_repair_without_outbox_and_anchor_dedup](../../tests/test_admission_graph_sync.py#L361)（1.718 秒）

### T34：否定、条件、能力与其他限定入图

完整 Fact 和 typed literal，不产生无条件肯定边

代码：[to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py), [to_neo4j/query_facts.py](../../to_neo4j/query_facts.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_qualified_literal_and_query](../../tests/test_admission_graph_sync.py#L318)（1.844 秒）
- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_t34_time_scope_applicability_quantifier_and_possibility_remain_full_facts](../../tests/test_admission_graph_sync.py#L248)（2.828 秒）

### T35：多候选并发更新同 Fact

按锁顺序更新和重算全部有效贡献

代码：[to_neo4j/fact_projection.py](../../to_neo4j/fact_projection.py)

- `passed` [tests.test_admission_graph_sync.RealGraphTests.test_graph_shared_fact_withdrawal_and_concurrency](../../tests/test_admission_graph_sync.py#L305)（1.593 秒）

### T36：简单多数 2 valid、2 invalid、1 uncertain

任何输入排列均 unresolved

代码：[evaluation/adapters/baselines.py](../../evaluation/adapters/baselines.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceEvaluationTests.test_t36_tied_majority_is_unresolved_for_every_order](../../tests/test_acceptance_gaps.py#L11)（0.016 秒）

### T37：超时、失败、标签缺失

冻结全集保留缺失结果，覆盖率用全集、准确性用标注子集

代码：[evaluation/manifest.py](../../evaluation/manifest.py), [evaluation/evaluate_review.py](../../evaluation/evaluate_review.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceEvaluationTests.test_t37_unlabeled_failed_and_timeout_rows_keep_population_denominator](../../tests/test_acceptance_gaps.py#L19)（0.0 秒）
- `passed` [tests.test_acceptance_gaps.AcceptanceEvaluationTests.test_t37_frozen_manifest_retains_missing_results_and_rejects_tampering](../../tests/test_acceptance_gaps.py#L34)（0.078 秒）

### T38：shadow 建议通过或批准扩展

不自动正式入库或发布

代码：[ontology_review/runtime.py](../../ontology_review/runtime.py), [ontology_review/publisher.py](../../ontology_review/publisher.py), [review_agent/admission.py](../../review_agent/admission.py)

- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_real_planner_tools_and_fixed_independent_committee_in_shadow](../../tests/test_ontology_extension_agent.py#L134)（4.969 秒）
- `passed` [tests.test_ontology_extension_agent.OntologyExtensionTests.test_automatic_publication_is_blocked_in_shadow](../../tests/test_ontology_extension_agent.py#L312)（5.328 秒）

### T39：扩展—重审反复触发

同一 case 总预算跨 Agent 与新 revision 保留，耗尽受控停止

代码：[review_agent/case_budget.py](../../review_agent/case_budget.py), [ontology_review/repository.py](../../ontology_review/repository.py)

- `passed` [tests.test_acceptance_gaps.AcceptanceOntologyTests.test_t39_published_revision_preserves_case_budget_and_stops_next_loop](../../tests/test_acceptance_gaps.py#L76)（5.797 秒）

### T40：合法初速/射程与正文假设句

不因短词或假设二字删除

代码：[extract_triplets/extrct.py](../../extract_triplets/extrct.py), [extract_triplets/pipeline_v4.py](../../extract_triplets/pipeline_v4.py)

- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_short_terms_and_symbols_are_not_deleted_or_forced_to_chinese](../../tests/test_source_candidate_v4.py#L184)（0.0 秒）
- `passed` [tests.test_source_candidate_v4.SourceCandidateV4Tests.test_body_hypotheses_and_captions_are_requested_instead_of_filtered](../../tests/test_source_candidate_v4.py#L193)（1.781 秒）

## 本轮补漏

- T13：本体审核契约升级为 v2；通用约束须明确引用定义、分类或范围证据，instance_only / unknown 不能批准。
- T18：人工拒绝或要求修订扩展时，同一当前订阅转为 schema_unresolved，不产生事实为假的标签。
- T19：影响清单扩展到类层级和关系的传递依赖，冻结候选修订、行版本及内容摘要；发布后为历史候选创建新修订和重审，撤回旧贡献。并发变化保留人工结果，预算耗尽时停用旧授权并明确记录。
- T36：平票明确返回 unresolved，不再偏向第一个最大选项。
- T37：冻结完整候选主表，保留缺失预测、超时、失败和未标注行；覆盖率使用全集，准确性使用标注子集；拒绝重复、外来或版本错配结果。
- 补充实际 Neo4j 测试：别名身份、两个来源跨度聚合、受影响历史贡献撤回，以及时间、范围、量词和可能性保留。

## 环境与执行边界

- Neo4j：`Neo4j/2025.11.2`；测试进程停止：`True`。
- 生产数据库、当前本体与原始本体未变：`True`。
- 独立测试数：61；缺失或跳过测试不会算作验收通过。
- 不运行真实模型质量实验，不自动迁移生产库或写入生产图谱。

本轮未覆盖整套阶段 F 实验设计；OWL 完整公理差异发布仍受原有适配边界约束。T02 验证的是显式保留/阻断未执行公理，并不声称具备未运行的推理器能力。
