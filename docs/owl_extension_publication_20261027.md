# OWL 扩展发布实现与验证

日期：2026-10-27。接续本体扩展审核 Agent 和统一准入，实现完整 OWL/Turtle 来源的受限差异发布。沿用已有 API、CLI、固定委员会、发布 CAS、幂等恢复与候选重审，不修改生产当前版本。

## 发布行为

`ontology_review/owl_delta.py` 从已校验、不可变的基础 release 加载完整 RDF 图，对规范化操作逐项编译。新版本包含 `ontology.ttl`、`owl_patch.json`、`ontology_snapshot.json`、应用 profile、IRI 映射、转换报告及原有审批和方法制品。项目 JSON 由新 Turtle 重新转换得到，禁止仅改项目视图而沿用旧 OWL 来源。

| 操作 | OWL 行为 |
| --- | --- |
| add_class | 服务端分配稳定 IRI/ID，添加声明、标签、定义和显式父类 |
| add_object_property | 添加声明、标签、定义及具名 domain/range；多项维持合取应用语义 |
| add_data_property | 添加数据属性声明、具名 domain 和支持的 XSD 数据类型；支持别名及完整 IRI 写法 |
| add_alias | 添加 SKOS altLabel，可显式指定语言 |
| revise_definition | 替换指定语言的定义；有多种语言时必须明确选择，其他语言及未替换的注释保留 |
| revise_constraint | 只替换指定字段中的具名父类/domain/range/数据类型；匿名约束保持原样 |
| merge_terms | 需要高影响人工确认；将被合并 IRI 在整个 RDF 图中替换为目标 IRI，保留两者约束与定义的并集，标签转别名，并记录退役 ID/IRI |

合并会明确影响引用该 IRI 的匿名表达式、注释及来源中的示例实例，实际增删均进入提案，不暗中丢弃被合并项的其他公理。来源中的实例仍只属于来源制品，不能因本体发布直接成为图谱事实。退役身份可从新 Turtle 重新导入恢复。

新实体 IRI 由基础 release、种类和规范标签确定，项目 ID 由 IRI 确定；无需旧映射文件重新导入 Turtle 仍得到相同 ID。相对 IRI 使用初始导入时记录的文件基址解析；不联网加载 `owl:imports`。

## 审核与完整性

- 本体检索扫描项目条目和完整来源 RDF，邻域工具包含相连匿名公理。搜索结果只用于召回，不充当语义等价或缺口证明。
- 独立提案包含完整变更前/后 RDF、规范化操作、逐项声明增删、来源与图摘要、编译器源码摘要、IRI 映射及 profile。规划循环减少重复传输，独立审核不裁剪这些来源。超过现有提案大小预算时受控停止，不静默截断。
- 发布时重新编译并逐项比对已审核提案；来源、视图、编译器或差异变化均要求重新审核。发布和加载 release 时重做来源/视图/profile 检查，校验补丁绑定并重放声明增删，与实际新图比较。
- 使用固定版本 RDFLib 7.1.4 的图规范化器与排序 N-Triples 输出；该输出也是合法 Turtle，不声称采用 RDFC-1.0。保留类型字面量的原始词法、多行字符串和语言标记，不修改进程全局字面量设置。旧制品缺少解析版本标记时使用兼容解析路径。
- 工作台直接显示声明增删、完整前后来源和未执行公理提示。高影响操作与 profile 差异沿用人工确认；旧 release 不覆盖，竞争发布必须 rebase 重审。
- V2 发布后创建候选新修订和新审核任务，不继承 V1 票。事实准入仍要求该修订下的新来源证据和 Schema 校验，再经贡献对账同步 Neo4j。

## 推理与运行边界

支持的是上表七类操作，没有开放任意 RDF 删除或 SPARQL 更新，也没有任意 OWL DL 表达式编辑器。未执行的限制、属性链、导入等公理完整保存在来源中，保守应用 profile 继续阻止自动准入。

推理器适配器接收实际新版本 Turtle，并要求结果绑定来源、图、差异、profile 和补丁摘要。没有配置实际推理器时为 `not_run`；结构检查和模型审批不提供 OWL 一致性结论。验证中的推理器回复是 Mock 协议测试，模型质量不在本轮评价范围。

## 验证结果

OWL 专项 **18/18 通过，无跳过**。其中包含真实隔离 Neo4j 的 V1 → V2 → 候选 revision 2 → 新独立三元组审核 → 人工新来源证据包接纳 → 正式贡献同步。另验证了声明保留、七类操作、语言选择、IRI 稳定性、复杂公理阻断、来源/视图/补丁篡改拒绝及推理器协议绑定。

可复查制品：

- [OWL 专项执行报告](../runs/owl_extension_validation/647d7f1bf0ab4532b43d2f053b2d97ff/validation_report.json)、[逐项测试日志](../runs/owl_extension_validation/647d7f1bf0ab4532b43d2f053b2d97ff/tests.log)。
- [新版本完整 Turtle](../runs/owl_extension_validation/647d7f1bf0ab4532b43d2f053b2d97ff/demo/releases/release_8cd0b4e3d185503f830599e4ef7f2058faf7a7342b11e686a991cf1e5cf7fcf8/ontology.ttl)、[冻结补丁及完整来源](../runs/owl_extension_validation/647d7f1bf0ab4532b43d2f053b2d97ff/demo/releases/release_8cd0b4e3d185503f830599e4ef7f2058faf7a7342b11e686a991cf1e5cf7fcf8/owl_patch.json)。演练新增 5 条声明、移除 0 条；旧来源字节未变，编译器摘要与最终源码一致。
- [Neo4j 实际事实与贡献](../runs/owl_extension_validation/647d7f1bf0ab4532b43d2f053b2d97ff/graph_facts.json)，仅接纳候选产生有效事实，OWL 示例实例未入图。测试服务器为 Neo4j/2025.11.2，已停止。
- [补充 OWL 场景后的 T01～T40 报告](../runs/acceptance/356fa30041f74c93bdcb81bd0b03614f/acceptance_report.json)：**40/40 通过，70 个独立测试，无跳过**；包含真实图谱行为和独立进程发布竞争。

全量 Python 回归共 342 项，**319 通过、23 跳过、0 失败**；23 项为常规回归未设置图环境时跳过的 Neo4j 测试，本次相关图链路由上方两个入口使用真实隔离服务器验证。见 [回归结果](../runs/owl_regression.json)及[完整日志](../runs/owl_regression.log)。四个已知测试生成文件按执行前字节恢复。前端现有 9 项测试通过，Vite 生产构建通过。

生产库仍为 2,645 个候选、1,610 次 audit、5,031 张票；逻辑摘要 `8ea70462dd9dec5bb0c54763f7f945c7f815b5135543ec2500884677f6cd3799`，当前生产 release 仍为 `release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71`。重现命令：

```powershell
python -m scripts.validate_owl_extension_publication --neo4j-home D:/ProgramData/neo4j-community-2025.11.2
python -m scripts.validate_acceptance_t01_t40 --neo4j-home D:/ProgramData/neo4j-community-2025.11.2
```

两个入口都使用独立 SQLite、release 根目录和隔离 Neo4j，不连接生产图谱，不调用真实模型。默认 shadow、自动接纳/发布/导入开关不变。运行中的服务需重启才能载入代码。
