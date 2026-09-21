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

启动统一工作台：

```powershell
python workbench_app.py
```

打开：

```text
http://127.0.0.1:8764
```

工作台一级导航包含：

- 本体抽取：嵌入 `ontology_build_app.py`
- 三元组抽取：选择 Markdown 后运行 `extract_triplets/extrct.py`
- 三元组审核：嵌入 `review_triplets_app.py`

## 单独运行

```powershell
python ontology_build_app.py
python extract_triplets\extrct.py
python review_triplets_app.py
python to_neo4j\save_to_neo4j.py
```

默认端口：

- 工作台：`8764`
- 三元组审核：`8765`
- 本体抽取：`8766`
