# 本体制品接入与发布

本次实现修改方案的“初始本体接入”部分：provider 接口、TTL/JSON 导入、受限 OWL 转换、不可变 release、显式抽取绑定。原 `ontologyBuild.pipeline` 保留为 legacy 候选生成入口。

没有在本次开发中执行作者生成脚本、调用真实模型或连接 Neo4j。当前已发布并登记受限 legacy V1：保留 49 类、14 个对象属性和 209 个数据属性，将 20 个冲突或悬空属性隔离待审，自动准入关闭。源 JSON 逐字节保留。此版本来自现有项目本体，不是 Extended NeOn-GPT 的新生成制品。本体扩展 Agent、文档坐标改造和候选 v4 属于后续阶段。完整范围与版本摘要见 [发布核对报告](ontology_initial_release_audit_20261026.md)。

## 依赖

Python 最低 3.10。在项目的 Python 环境安装：

```powershell
python -m pip install -r ontologyBuild/requirements-import.txt
```

本机验证使用 Python 3.12.14，RDFLib 7.1.4 安装在项目 `.runtime-packages`。使用该目录时，应把它与项目根目录加入 `PYTHONPATH`。

## 先导入检查

```powershell
python -m scripts.import_ontology --source path/to/ontology.ttl --provider neon --method-manifest path/to/method_manifest.json --validation-report path/to/validation_report.json --requirements path/to/requirements.json --competency-questions path/to/competency_questions.json
```

只有 `--source` 必填，其余制品可以逐步补齐。缺失的外部校验标记 `not_run`，缺失来源标记 `not_provided`，不会声称作者工具或推理器已运行。提供的上游 manifest 原样保存在 `upstream`，其来源仍为 `supplied_unverified`；它应包含作者仓库 commit、实际执行方法、模型标识、生成参数、工具版本和制品位置，不要包含密钥。

项目 JSON 使用 `--provider legacy`；`auto` 按 `.json` 后缀选择 legacy，其余选择 Turtle。

默认输出到新的 `runs/ontology_imports/<id>/`。这里是检查草稿，包含转换和校验报告及 `approval.status=pending`，不含 `release_manifest.json`。`valid=true` 表示项目结构可接受，不代表领域语义正确，也不代表可以自动审核。`auto_review_allowed` 另行报告。

本地样例可直接检查：

```powershell
python -m scripts.import_ontology --source tests/fixtures/ontology_import.ttl
```

## 显式发布

检查来源、转换差异和报告后，在同一导入命令追加操作人和批准理由：

```powershell
python -m scripts.import_ontology --source path/to/ontology.ttl --provider neon --method-manifest path/to/method_manifest.json --validation-report path/to/validation_report.json --publish --approved-by operator_name --approval-reason "已核验来源、语义及应用约束"
```

发布默认写入 `runs/ontology_releases/release_<完整摘要>/`，不会覆盖 `ontology/ontology.json`。仅 `--publish` 生成不可变制品；追加 `--activate` 才登记为当前版本。非首次激活必须用 `--expected-base-release-id <当前发布号>` 绑定基线，旧基线会被拒绝；完全相同制品和授权的重试保持幂等。结构错误、重复定义冲突或明确失败的外部校验会阻止发布。存在未执行的复杂 OWL 公理时，可以发布供检查与人工处理的受限制品，但自动审核保持阻断。

旧 JSON 存在未解决定义时，可显式追加 `--quarantine-invalid --confirm-profile-differences`，创建受限应用 profile：完整来源保留，问题属性和未声明子类不进可执行部分，不创建占位类，也不在冲突定义中任选一个。差异确认同时绑定 profile 和 mapping report 摘要。该 profile 自动准入关闭。

release 保存来源 TTL 或 JSON、snapshot、application profile、IRI 映射、转换报告、需求、能力问题、方法来源、校验报告、批准记录和 release manifest。`ontology_version` 是 snapshot 规范 JSON 的 SHA-256；`profile_hash` 绑定实际应用约束；`release_id` 绑定全部制品和批准依据。同一输入和批准记录重复发布不改写文件；内容变化产生新目录。加载时复算 manifest 和每个制品的摘要，文件缺失或篡改即失败。这里提供完整性校验，不提供数字签名或操作人身份认证。

`python -m scripts.freeze_ontology --input path/to/reviewed_schema.json` 继续支持显式选择旧 JSON 基线，现在同样发布完整 release。旧的仅有 snapshot 的目录不会被自动升级成新 release。

## 指定给抽取

```powershell
python -m scripts.run_ontology_then_extract --input path/to/document.md --output-stem sample --llm-config ontology_generate/config/ontology_config.yaml --chain-id sample_release_run --ontology-release runs/ontology_releases/release_<摘要>
```

传入 release 后跳过旧本体候选生成，校验制品，并把 release 路径显式传给抽取器；`--ontology-release current` 使用已登记的当前版本。未传 release 时，串联入口优先取得当前版本；没有当前版本才运行旧候选生成，并停在 `awaiting_ontology_release`，退出码 2，不继续读旧本体抽取。

抽取器也支持直接使用：

```powershell
python -m extract_triplets.extrct --input path/to/document.md --output-stem sample --ontology-release runs/ontology_releases/release_<摘要> --review-mode shadow
```

`--ontology-release` 与 `--ontology` 互斥；也可使用 `TRIPLET_ONTOLOGY_RELEASE`。两者未指定时，抽取器优先使用已登记的当前版本。显式 `--ontology` 覆盖当前版本及环境 release，保留旧调用兼容性，此时运行标记 `legacy_unreleased`，不冒充已发布版本。工作台启动抽取时必须取得发布版本，不再回退到旧 JSON。

本体页面新增导入预览、差异确认、显式发布和历史记录。后端接口为 `POST /api/ontology/import`、`POST /api/ontology/releases`、`GET /api/ontology/releases`。预览冻结完整导入对象；发布时绑定预览摘要和当时的基线，返回 409 表示基线变化，需要重新检查。

抽取保存整套 release 副本到自己的 run 目录，并在 manifest 和候选 JSON 中保存 `ontology_release_id`、`ontology_version`、`profile_hash` 及本地制品路径。现有 SQLite JSON 字段可以保存这些增量字段，本步骤不迁移或覆盖历史数据库。

## OWL 支持范围

- 具名类、RDFS/SKOS 标签与定义、声明的别名、IRI、多父类、对象属性、数据属性和具名 domain/range。同名关系存在歧义时返回未知，不按最后一个同名关系静默映射。
- 多条 OWL domain/range 按全部满足检查；旧项目 JSON 默认保留 `allowed_any`。OWL domain/range 原本是推断语义，本项目 profile 明确把它用于应用准入约束，不声称是 OWL 逻辑蕴涵检查。
- XSD 范围进入 datatype 检查，不当作 class；支持常用字符串、整数及范围、布尔、小数、有限浮点、日期时间和语言字面量。未实现类型返回 unknown；多重数据类型范围阻断自动审核。日期等类型的检查是保守子集，不是完整 XSD 校验器。
- 复杂类表达式、属性特征、属性层级、外部 imports 等保留在原 TTL 和报告中，不假装执行。首版采用保守策略：存在影响准入的未支持语义时，阻断整个 profile 的自动审核，尚未细分到最小影响子图。imports 不联网自动加载。
- 作者工具示例实例仅保留在源 TTL，不转成候选事实。
- 顶层与类内同 ID 属性显式定义冲突会报错，不能借兼容适配器合并后掩盖差异。类名歧义、悬空引用和层级环也会阻止发布。

实现依据：[作者仓库](https://github.com/NadeenAhmad/neon-gpt-extended)、[W3C OWL 2 Primer](https://www.w3.org/TR/owl2-primer/#Domain_and_Range_Restrictions)。当前只接入作者方法的制品，不含作者脚本自动化封装。

## 验证与现有本体诊断

```powershell
python -m unittest discover -s tests -p test_ontology_import.py -v
python -m unittest discover -s tests -p "test_*.py"
```

新增测试覆盖导入不发布、V1/V2 不可变、完整制品绑定、篡改、复杂公理阻断、双 domain、多父类、字面量、示例实例隔离、旧候选生成不误用旧本体以及运行和候选的版本绑定。使用固定 TTL、模拟子进程和独立 SQLite；不代表真实模型质量或作者实现已复现。

2026-10-26 第一轮接入验证为 166 项回归通过；本次发布补漏的最新结果记录在 [发布核对报告](ontology_initial_release_audit_20261026.md)。全量测试在沙箱内会因现有测试使用的临时目录访问受限而失败，获准在沙箱外重跑。前端构建同样需要允许 esbuild 启动子进程。仍有原代码的 `datetime.utcnow()` 弃用提示。

对现有 `ontology/ontology.json` 的原始只读诊断为 49 类、243 个合并后的属性，发现 26 项悬空类引用和 32 条原始属性定义冲突诊断（已合并转换与项目校验对同一冲突的重复报告）。冲突计数是诊断条目数，不是去重属性数。原报告见 [基线诊断报告](ontology_baseline_diagnostics_20261026.json)。原件未修改；本次通过显式隔离并确认差异的受限 profile 发布 V1，问题定义仍需单独审核。
