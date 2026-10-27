"""Explicit T01--T40 evidence mapping. A skipped or missing test never passes a row."""

def entry(number,scenario,expected,module,tests,code,*,graph=False):
    return {'id':f'T{number:02}','scenario':scenario,'expected':expected,
        'tests':[module+'.'+test for test in tests],'code':code,'requires_real_neo4j':graph}

I='tests.test_ontology_import.OntologyImportTests'
R='tests.test_ontology_release.OntologyReleaseTests'
S='tests.test_source_candidate_v4.SourceCandidateV4Tests'
E='tests.test_review_enhancement_v4.EnhancedAgentTests'
L='tests.test_review_enhancement_v4.IssueLifecycleTests'
O='tests.test_ontology_extension_agent.OntologyExtensionTests'
A='tests.test_admission_graph_sync.AdmissionTests'
G='tests.test_admission_graph_sync.RealGraphTests'
N='tests.test_acceptance_gaps.AcceptanceOntologyTests'
V='tests.test_acceptance_gaps.AcceptanceEvaluationTests'

CASES=[
entry(1,'初始本体仅生成候选，未发布','构建成功不冒充发布，不使用旧本体继续抽取',I,
 ['test_import_alone_has_no_release_and_tools_are_not_claimed_run','test_successful_legacy_generation_never_extracts_with_an_old_ontology'],['ontologyBuild/release_service.py','scripts/run_ontology_then_extract.py']),
entry(2,'OWL domain 与 allowed_any 不同','合取语义显式处理，未执行公理阻止自动使用',I,
 ['test_multiple_domains_are_conjunctive_and_all_parents_survive','test_unexecuted_restriction_is_preserved_and_blocks_auto_review'],['ontologyBuild/owl_to_project_schema.py','review_agent/ontology_guard.py']),
entry(3,'数据属性 range 为 XSD','保留数据类型并校验词法，不创建悬空类',I,['test_xsd_range_is_a_datatype_and_lexical_values_are_checked'],['ontologyBuild/owl_to_project_schema.py','review_agent/ontology_guard.py']),
entry(4,'label 与标准关系 ID','批准别名、label 和 ID 映射后同身份，并实际入图',G,['test_t04_label_alias_and_id_share_one_imported_fact'],['schema/relation_registry.py','to_neo4j/fact_projection.py'],graph=True),
entry(5,'相同头尾，不同条件/极性/模态','完整限定决定独立事实键',S,['test_qualifiers_and_typed_literal_identity_are_not_collapsed'],['review_agent/contracts.py','schema/fact_identity.py']),
entry(6,'漏填极性','保持 unknown，不默认 positive',S,['test_missing_polarity_and_status_remain_unknown'],['extract_triplets/candidate_contract.py']),
entry(7,'两来源抽出同一事实','保留独立候选及证据锚点，审核后聚合一个 Fact',G,['test_t07_distinct_source_spans_preserved_in_one_fact'],['extract_triplets/candidate_contract.py','to_neo4j/fact_projection.py'],graph=True),
entry(8,'子本体没有，完整本体已有','查询完整本体并映射，阻止重复扩展',E,['test_nullable_full_lookup_finds_existing_mapping_and_blocks_duplicate'],['review_agent/tools.py','schema/relation_registry.py']),
entry(9,'原文支持，本体缺失','原文共识成立并进入扩展等待',E,['test_source_supported_schema_gap_creates_waiting_task_without_outbox'],['review_agent/consensus.py','review_agent/lifecycle_repository.py']),
entry(10,'三元组 uncertain 仅因 Schema 缺口','保留来源支持，不判事实假',E,['test_source_supported_schema_gap_creates_waiting_task_without_outbox','test_schema_only_failure_cannot_claim_source_rejection'],['review_agent/response_contract.py','review_agent/consensus.py']),
entry(11,'原文不足','缺少独立来源支持不能创建扩展请求',N,['test_t11_insufficient_source_cannot_trigger_schema_expansion'],['review_agent/tools.py']),
entry(12,'原文支持，怀疑既有约束错误','保留 guard 原失败并进入约束复核',E,['test_constraint_review_retains_guard_failure_and_never_approves_ontology'],['review_agent/ontology_guard.py','review_agent/tools.py']),
entry(13,'一实例泛化为通用约束','必须引用定义/分类/范围依据，实例证据不能批准',N,['test_t13_instance_only_cannot_approve_general_constraints','test_t13_definition_grounding_is_required_and_can_pass'],['ontology_review/contracts.py','ontology_review/validators.py','prompts/ontology_extension_validator_v1.txt']),
entry(14,'同一扩展由多个候选触发','去重扩展任务，保留独立订阅及成本',O,['test_shared_task_has_two_subscriptions_and_charges_both_cases'],['review_agent/lifecycle_repository.py','review_agent/case_budget.py']),
entry(15,'两个提案都基于 V1，先后发布','旧 base CAS 失败，必须 rebase 和重新审核',O,['test_release_head_race_requires_rebase_and_fresh_review'],['ontologyBuild/release_catalog.py','ontology_review/publisher.py']),
entry(16,'delta 变化沿用旧票','摘要和差异修订不匹配，拒绝发布',O,['test_new_delta_invalidates_old_round_and_stale_approval'],['ontology_review/contracts.py','ontology_review/publisher.py']),
entry(17,'发布重复通知与候选已变更','幂等唤醒，不复用旧票、不重启过期订阅',O,
 ['test_publication_is_idempotent_and_never_rebinds_twice','test_rebound_candidate_uses_new_proposal_and_never_old_votes','test_revocation_between_release_commit_and_wakeup_keeps_human_change'],['ontology_review/publisher.py','review_agent/repository.py']),
entry(18,'扩展被人工拒绝','候选进入 schema_unresolved，来源支持和真值标签保留',N,['test_t18_operator_rejection_updates_subscribers_without_false_truth_label'],['ontology_review/repository.py','ontology_review/service.py']),
entry(19,'domain/range 或祖先语义变化影响历史候选','列出传递影响，创建新修订和重审，撤回旧图贡献',G,['test_t19_schema_impact_withdraws_previously_projected_history'],['ontology_review/impact.py','ontology_review/publisher.py','to_neo4j/reconciler.py'],graph=True),
entry(20,'人工提交空、假、跨候选和过期证据','无正式贡献；非法证据 422，并发版本冲突 409',A,
 ['test_nonempty_exact_human_evidence_required','test_bundle_stale_and_foreign','test_http_evidence_errors_and_conflicts'],['review_agent/human_evidence.py','review_agent/admission.py','workbench_app.py']),
entry(21,'人工选择合法新原文','服务端从规范全文核验，绑定新人工证据包后允许裁决',A,
 ['test_bound_acceptance_and_idempotency','test_http_evidence_errors_and_conflicts'],['review_agent/human_evidence.py','review_agent/admission_repository.py']),
entry(22,'正确候选首轮缺上下文','真实补证和复核后解除证据不足',E,['test_actual_react_rechecks_new_evidence_and_clears_only_insufficiency'],['review_agent/issue_ledger.py','review_agent/runtime.py']),
entry(23,'候选本身漏条件','旧表达不能靠补证通过，须新修订',E,['test_condition_error_cannot_be_washed_away_by_new_support','test_new_audit_does_not_erase_a_revision_bound_condition_issue'],['review_agent/issue_ledger.py','review_agent/action_policy.py']),
entry(24,'规划器声称反证已解决','文本声明和同一证据不能清除义务',L,
 ['test_same_evidence_and_planner_text_cannot_resolve','test_all_protected_references_must_be_independently_rechecked'],['review_agent/issue_ledger.py']),
entry(25,'首轮反证，次轮支持','精确选定轮次和提案，拒绝内容篡改和历史混票',E,
 ['test_conflict_is_resolved_by_current_independent_checks_and_import_uses_only_selected_round','test_exact_selection_detects_changed_evidence_and_ignores_other_rounds'],['review_agent/proposal_resolver.py','review_agent/consensus.py']),
entry(26,'三席一超时、两 valid','固定三席分母，不能形成正式共识',N,['test_t26_three_seats_one_timeout_do_not_form_triple_consensus'],['review_agent/consensus.py','review_agent/validators.py']),
entry(27,'同一模型不同别名','实际身份去重，不增加独立票',E,
 ['test_reported_same_actual_model_does_not_get_distinct_seats','test_duplicate_model_aliases_do_not_form_independent_source_consensus'],['review_agent/consensus.py']),
entry(28,'三元组票用于本体发布','拒绝跨任务、契约和版本票据重放',O,['test_triple_ticket_cannot_be_used_as_ontology_approval'],['ontology_review/validators.py','ontology_review/contracts.py']),
entry(29,'同名标题、英文多句、跨块条件','章节位置、句界、上下文保持正确',S,
 ['test_identical_repeated_headings_have_distinct_positions','test_english_decimal_abbreviation_and_fenced_heading_boundaries','test_context_crosses_chunk_boundaries_without_crossing_sections'],['document/hierarchy_index.py','document/snapshot.py']),
entry(30,'保留版本号，篡改正文或偏移','摘要与结构验证失败',S,['test_tampering_text_offsets_or_structure_fails_without_version_change'],['document/index_integrity.py','evidence/verification.py']),
entry(31,'revision1 入库→修改2→快速再通过2','停用旧贡献，清理旧 Fact，只有新修订有效',G,['test_graph_changed_fact_retires_old_fact_even_with_stale_retract'],['review_agent/admission_repository.py','to_neo4j/fact_projection.py'],graph=True),
entry(32,'两候选支持同 Fact，撤回一条','另一贡献仍有效',G,['test_graph_shared_fact_withdrawal_and_concurrency'],['to_neo4j/fact_projection.py'],graph=True),
entry(33,'outbox 乱序/重复/提交后崩溃','幂等收敛，旧 worker 不能回退新版本',G,
 ['test_graph_duplicate_reorder_and_old_worker','test_graph_atomic_rollback_and_crash_retry','test_graph_periodic_repair_without_outbox_and_anchor_dedup'],['to_neo4j/reconciler.py','to_neo4j/fact_projection.py'],graph=True),
entry(34,'否定、条件、能力与其他限定入图','完整 Fact 和 typed literal，不产生无条件肯定边',G,
 ['test_graph_qualified_literal_and_query','test_t34_time_scope_applicability_quantifier_and_possibility_remain_full_facts'],['to_neo4j/fact_projection.py','to_neo4j/query_facts.py'],graph=True),
entry(35,'多候选并发更新同 Fact','按锁顺序更新和重算全部有效贡献',G,['test_graph_shared_fact_withdrawal_and_concurrency'],['to_neo4j/fact_projection.py'],graph=True),
entry(36,'简单多数 2 valid、2 invalid、1 uncertain','任何输入排列均 unresolved',V,['test_t36_tied_majority_is_unresolved_for_every_order'],['evaluation/adapters/baselines.py']),
entry(37,'超时、失败、标签缺失','冻结全集保留缺失结果，覆盖率用全集、准确性用标注子集',V,
 ['test_t37_unlabeled_failed_and_timeout_rows_keep_population_denominator','test_t37_frozen_manifest_retains_missing_results_and_rejects_tampering'],['evaluation/manifest.py','evaluation/evaluate_review.py']),
entry(38,'shadow 建议通过或批准扩展','不自动正式入库或发布',O,
 ['test_real_planner_tools_and_fixed_independent_committee_in_shadow','test_automatic_publication_is_blocked_in_shadow'],['ontology_review/runtime.py','ontology_review/publisher.py','review_agent/admission.py']),
entry(39,'扩展—重审反复触发','同一 case 总预算跨 Agent 与新 revision 保留，耗尽受控停止',N,['test_t39_published_revision_preserves_case_budget_and_stops_next_loop'],['review_agent/case_budget.py','ontology_review/repository.py']),
entry(40,'合法初速/射程与正文假设句','不因短词或假设二字删除',S,
 ['test_short_terms_and_symbols_are_not_deleted_or_forced_to_chinese','test_body_hypotheses_and_captions_are_requested_instead_of_filtered'],['extract_triplets/extrct.py','extract_triplets/pipeline_v4.py'])
]

# Cross-layer evidence supplements one acceptance row without weakening its primary checks.
CASES[14]['tests'].append(R+'.test_independent_processes_cannot_both_publish_against_the_same_base')
CASES[18]['tests'].extend([N+'.test_t19_domain_range_change_reaudits_previously_accepted_history',N+'.test_t19_indirect_historical_impact_creates_revision_and_reaudit'])
CASES[24]['tests'].append(G+'.test_t25_v4_auto_admission_imports_only_current_round_assessments')
CASES[24]['requires_real_neo4j']=True
CASES[37]['tests'].append(N+'.test_t38_shadow_triple_with_ready_policy_and_consensus_creates_no_contribution')
CASES[20]['tests'].append(N+'.test_t21_human_can_review_rebound_revision_with_new_bound_source_bundle')
CASES[20]['code'].append('review_agent/admission.py')

# OWL source publication supplements the original JSON-based release scenarios.
W='tests.test_owl_extension_publication.OwlDeltaTests'
P='tests.test_owl_extension_publication.OwlPublicationFlowTests'
CASES[1]['tests'].extend([W+'.test_additive_patch_preserves_anonymous_axioms_annotations_imports_and_literal_terms',W+'.test_named_constraint_revisions_keep_anonymous_restrictions_and_all_semantics'])
CASES[1]['code'].extend(['ontology_review/owl_delta.py','ontologyBuild/rdf_graph.py'])
CASES[2]['tests'].append(W+'.test_add_class_data_property_and_alias_round_trip_with_original_ids')
CASES[15]['tests'].extend([W+'.test_complete_rdf_context_and_blank_node_delta_replay_are_verified',P+'.test_edited_source_patch_or_view_cannot_use_previous_ontology_votes'])
CASES[15]['code'].append('ontologyBuild/rdf_artifact.py')
CASES[16]['tests'].append('tests.test_owl_extension_publication.OwlGraphFlowTests.test_only_admitted_owl_candidate_reaches_graph_and_abox_stays_in_source')
CASES[16]['requires_real_neo4j']=True
