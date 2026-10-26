# knowledge-graph

本仓库提供知识图谱三元组抽取、本体候选审核，以及可追溯的 v3 三元组审核机制。v3 以 SQLite 为权威数据源，把候选版本、证据、模型投票、人工反馈、入库事件和撤回记录绑定在同一条审计链中；旧 JSON 仅保留为兼容视图或导出物。

## 安装与工作台

需要 Python 3.10+。在项目根目录安装依赖：

```powershell
cd D:\knowledge-huopao
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install openai langchain-text-splitters tqdm pydantic neo4j scikit-learn joblib
```

首次运行还需构建 Vue 前端（需要 Node.js 和 npm）：

```powershell
cd frontend\workbench
npm install
npm run build
cd ..\..
```

启动统一工作台并打开 `http://127.0.0.1:8770`：

```powershell
python workbench_app.py
```

工作台包含三元组抽取/审核和本体抽取/审核。修改 Vue 源码时可在 `frontend/workbench` 运行 `npm run dev`；正式访问 8770 端口前需要重新运行 `npm run build`。

独立服务入口：

```powershell
python review_triplets_app.py       # 三元组审核，默认 8765
python ontology_build_app.py        # 本体审核，默认 8766
```

## v3 审核配置与模式

复制配置模板后按实际环境修改：

```powershell
Copy-Item config\review_agent.example.json config\review_agent.json
$env:KG_REVIEW_CONFIG="config/review_agent.json"
python -m review_agent.cli init-db
```

模板中的四个 validator 是固定委员会槽位。真实模型必须分别配置模型名、模型家族、API key 和 OpenAI-compatible base URL，例如：

```powershell
$env:KG_JUDGE_A_MODEL="实际模型名"
$env:KG_JUDGE_A_FAMILY="实际模型家族"
$env:KG_JUDGE_A_API_KEY="实际 key"
$env:KG_JUDGE_A_BASE_URL="https://example.test/v1"
```

其余 B/C/D 槽位使用对应的 `KG_JUDGE_B_*`、`KG_JUDGE_C_*`、`KG_JUDGE_D_*`。服务状态接口只返回是否就绪和模型标识，不返回凭证。

当前 HTTP 服务是本地单用户模式，审核人固定取配置中的 `local_operator`；前端提交的自由文本不会被当作已鉴权身份。

模式含义：

- `legacy`：仅用于复现旧机制；旧评分和旧状态不能获得 v3 入库资格。
- `assist_only`：模型不足完整委员会时只提供辅助信息，最终由人工决定。
- `shadow`：完整记录新机制建议，但语义接纳仍交给人工；这是默认模式。
- `active`：只有委员会、已验证校准器和冻结策略都就绪时才允许创建主动审核任务，否则返回 503。
- `offline_demo`：仅使用 Mock 客户端和隔离数据库，不访问外部模型。

冷启动严格 fail closed：未训练的校准器返回 `calibrated_probability=null`，默认策略关闭自动接纳，设置 `mode=active` 本身不会绕过证据、共识、校准和策略检查。

当前模板明确使用 `planner.strategy=deterministic`，实现的是有轮次、工具预算和停止条件的 bounded state machine；审计记录会保存该实验标识，不把确定性编排冒充为 LLM 自主规划器。

## 抽取、审核和导出

三元组抽取使用模块入口。命令行参数优先于配置，配置优先于兼容环境变量：

```powershell
python -m extract_triplets.extrct `
  --config config/review_agent.json `
  --review-mode shadow `
  --input mdfiles\your_document.md `
  --output-stem your_document
```

兼容变量包括 `TRIPLET_MARKDOWN_FILE`、`TRIPLET_OUTPUT_JSON`、`TRIPLET_REJECTED_JSON`、`TRIPLET_EVIDENCE_MD`、`TRIPLET_BOOK_NAME`、`TRIPLET_MODEL`、`OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。每次抽取会创建独立 `run_id`，冻结原文索引和本体快照，并把候选初始版本写入 SQLite。

常用 v3 命令：

```powershell
python -m review_agent.cli audit --run-id <run_id> --mode shadow
python -m review_agent.cli export --run-id <run_id>
python -m review_agent.cli build-feedback-dataset --output runs\v3\feedback.json
python -m review_agent.cli build-memory --output runs\v3\memory_snapshot.json
python -m review_agent.cli update-reliability --dataset runs\v3\feedback.json --output runs\v3\reliability.json
```

`<run_id>` 是占位符，需要替换为真实运行 ID。`modify` 会创建不可变的新 revision 并重新进入审核，不会在原候选上覆盖后直接接纳。`defer` 和非事实性 reject 不生成真假标签。人工提交携带幂等键和 `expected_row_version`；版本冲突返回 409。

统一工作台的 v3 API 前缀为 `/api/triplets`，主要接口包括：

- `GET /runs`、`GET /items?run_id=...`、`GET /summary?run_id=...`
- `GET /candidates/{candidate_id}`、`GET /candidates/{candidate_id}/audit`
- `GET /evidence/{evidence_id}/context`、`GET /config-status`
- `POST /audit-jobs`、`POST /audit-jobs/{job_id}/cancel`
- `POST /candidates/{candidate_id}/reaudit`、`POST /decision`

独立审核服务暴露同一服务层，路径去掉 `/triplets`，例如 `/api/runs` 和 `/api/decision`。旧文件参数接口仍可只读兼容，任何新决定都会事务化写入 v3 数据库。

## 本体冻结

审核任务必须绑定本体内容哈希。发布前先验证并冻结快照：

```powershell
python -m scripts.freeze_ontology --input ontology\ontology.json
```

也可调用 `POST /api/ontology/freeze`。冻结失败会返回结构化错误，不会把候选审核记录直接当作正式本体发布证明。

现有本体候选流水线仍可单独验证：

```powershell
python -m ontologyBuild.pipeline --input tests\fixtures\ontology_sample.md --mock --max-chunks 5 --base-policy draft
python -m ontologyBuild.base_ontology_audit --input ontology\ontology.json --report-dir runs\ontology_audit
```

真实本体抽取需设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `ONTOLOGY_MODEL`。`ONTOLOGY_BASE_POLICY=strict` 会在基础本体审计失败时停止；`draft` 只生成候选草案，草案不会自动发布到 `ontology/ontology.json`。

## 历史数据迁移

先检查，不改旧文件：

```powershell
python -m scripts.migrate_review_v3 --dry-run
```

确认报告后再应用：

```powershell
python -m scripts.migrate_review_v3 --apply
```

迁移读取现有 `triplets`、`rejected_triplets`、`review_logs`、`runs/v2` 和 evidence 文件；缺失来源会跳过并报告。apply 只新增 v3 记录和迁移清单，原文件不变。来源哈希与记录定位组成幂等键。历史自动接纳只记为 `legacy_accept_unverified`，不会生成新的合格审核决定；无法恢复原文位置时证据状态为 unavailable。

## 校准、策略与评估

从人工确认事件构建标签，并按冻结 split 训练校准器：

```powershell
python -m scripts.build_label_dataset --database runs\v3\review.sqlite3 --output runs\v3\labels.json
python -m scripts.train_calibrator --dataset runs\v3\labels.json --split-manifest runs\v3\split.json --output runs\v3\calibrator.joblib
python -m scripts.build_risk_policy --dataset runs\v3\policy_validation.json --data-version validation_v1 --output runs\v3\risk_policy.json
python -m evaluation.evaluate_calibration --help
python -m evaluation.evaluate_review --input runs\v3\evaluation.json
```

训练集标签与审核时的 `candidate_id + revision + feature snapshot` 绑定，修改前后的样本不会串联。只有达到冻结策略的目标精度、最小选中样本量和置信区间要求后，才应在配置中填写校准器/策略路径并打开 `auto_accept_enabled`。

当前 P0—P6 不包含 GraphJudge 的真实复现；`evaluation/adapters` 只提供实验适配接口和明确命名的普通基线，不能把一次 Prompt 裁判标成 GraphJudge。

## Neo4j 准入、撤回与查询

先查看 outbox 计划，不连接 Neo4j：

```powershell
python -m to_neo4j.save_to_neo4j --run-id <run_id> --dry-run
```

确认后配置连接并应用：

```powershell
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="你的密码"
python -m to_neo4j.save_to_neo4j --run-id <run_id> --apply
```

导入器只信任当前 revision 对应的最新有效 v3 决定，并校验关系 ID 是否在冻结本体白名单中。事实、证据、决定和候选贡献在事务中写入；之后的拒绝会生成 retract 事件，只有当同一事实没有其他有效贡献时才将事实和直接边标为 inactive。

下游 GraphRAG 必须只读取 active 事实。例如：

```cypher
MATCH (h:Entity)-[r]->(t:Entity)
WHERE r.active = true AND r.fact_key IS NOT NULL
RETURN h, r, t
```

不要把旧 `review_status`、`decision` 或 heuristic probability 当作入库凭证。

## 离线演示与测试

离线演示覆盖正确事实、否定错误、本体缺口、反证冲突、修改新版本和反馈幂等：

```powershell
python -m review_agent.cli demo `
  --fixture tests\fixtures\review_agent\sensor_demo.json `
  --database runs\v3\offline_demo.sqlite3
```

回归测试和前端构建：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
cd frontend\workbench
npm run build
```

工程测试只验证调用链、约束、事务和页面接口按预期工作，不代表真实模型已达到某个准确率；真实质量需要冻结样本上的人工评估。

## 本体制品接入

TTL/OWL 与旧项目 JSON 的导入、校验、不可变 release 和抽取版本绑定已提供。详见 [本体接入说明](docs/ontology_import.md)和[初始版本发布核对报告](docs/ontology_initial_release_audit_20261026.md)。串联入口、抽取器和工作台使用显式指定或已登记的当前 release；候选生成成功不代表版本已发布。

## 回退

回退时把配置模式切回 `shadow` 或 `assist_only`，并关闭 `routing.auto_accept_enabled` 和 `neo4j.auto_import_enabled`。保留 v3 SQLite、冻结快照和审计记录，不删除历史，也不要把旧 heuristic probability 接回自动入库。推荐上线顺序是 `offline_demo → shadow → 人工标注与校准 → 小范围 active`。
