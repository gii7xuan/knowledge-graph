# knowledge-graph

## 工作台入口

安装 Python 3.10+ 后，在项目根目录创建环境并安装依赖：

```powershell
cd D:\knowledge-huopao
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install openai langchain-text-splitters tqdm pydantic neo4j scikit-learn joblib
```

配置模型接口：

```powershell
$env:OPENAI_API_KEY="你的 key"
$env:OPENAI_BASE_URL="你的 base_url"
```

首次运行先构建 Vue 前端（需要 Node.js 和 npm）：

```powershell
cd frontend\workbench
npm install
npm run build
cd ..\..
```

启动统一工作台：

```powershell
python workbench_app.py
```

打开：

```text
http://127.0.0.1:8770
```

工作台由一个 Python 服务提供 API 和前端静态文件，一级导航包含：

- 三元组抽取审核：选择 Markdown 抽取三元组，并分页审核待审三元组
- 本体抽取审核：生成候选本体和审核队列，并分页审核候选项

修改 Vue 源码时可在另一个终端运行 `npm run dev`，开发地址由 Vite 输出；`/api` 会转发到运行中的 Python 工作台。正式访问 `8770` 端口前，需要重新运行 `npm run build`。

## 单独运行

```powershell
python ontology_build_app.py
python extract_triplets\extrct.py
python review_triplets_app.py
python to_neo4j\save_to_neo4j.py
```

默认端口：

- 工作台：`8770`
- 三元组审核：`8765`
- 本体抽取：`8766`
