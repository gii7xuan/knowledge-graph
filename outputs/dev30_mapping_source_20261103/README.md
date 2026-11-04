# 本轮结果

DEV-020后续：[本体审核与开发发布门槛结果](../dev020_release_20261103/DEV-020处理结果.md)。已收窄具体属性并修复基础定义；当前最新草案2席批准、Qwen结果未知，能力问题引文未闭合，尚未发布。下述内容为此前阶段记录。

[正式映射与新版本来源审核结果](正式映射与来源审核结果.md) 是本轮主报告。verification_and_summary.json保存版本、证据、票和完整性核对结果。

- DEV-004、013、020已在新的开发副本中提交修订，并完成完整新版本来源审核，三席均支持原文。DEV-004、013本体约束通过；DEV-020等待本体扩展。
- DEV-017已按用户选择收口：复用本体已有的风偏定义，并绑定准确原文证据，不生成部件三元组。结果存于DEV-017_definition_reuse_resolution.json及开发数据库的development_case_resolutions表；历史分歧票保留，未重抽或改票。
- DEV-020_agent_prepared_owl是本体准备Agent的实际当前草案及检查记录。结构和影响通过，本地能力问题unknown，独立本体审核及发布未执行。DEV-020_unapproved_data_property是前期手工最小探测稿，定义和注释语言不同，哈希与检查记录不能混用。

所有写入限于本目录开发数据库。原填写Excel、生产320条候选、生产人工状态、前次开发副本、本体正式版本和Neo4j保持不变。本轮模型结果不作为人工准确率。

入口脚本：runs/continue_dev30_mapping_source_20261103.py。默认只准备；--run在开发副本审核映射并提交已获批准的新版本来源审核。映射的有效票不会重复投票。source_batch_pre_dispatch_failure.json保留Windows IPC在源请求发出前失败的记录，随后已恢复运行，没有重发未知请求。

本体准备入口：runs/prepare_dev020_ontology_agent_20261103.py，仅准备草案和本地检查，不派发本体委员票，不发布。核对入口：runs/summarize_dev30_mapping_source_20261103.py，仅读取、核对并生成本轮报告。

DEV-017收口入口：runs/resolve_dev017_definition_reuse_20261103.py，核对既有OWL定义和准确原文后持久化用户选择。最新人工归一化记录为../dev30_analysis_20261103/annotations_scoped_v3.json；原Excel和原标注不改。
