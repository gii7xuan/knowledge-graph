import atexit
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
MDFILES_DIR = ROOT / "mdfiles"
WORKBENCH_RUN_DIR = ROOT / "runs" / "workbench"

WORKBENCH_HOST = "127.0.0.1"
WORKBENCH_PORT = int(os.environ.get("WORKBENCH_PORT", "8764"))
ONTOLOGY_BUILD_PORT = int(os.environ.get("ONTOLOGY_BUILD_PORT", "8766"))
TRIPLET_REVIEW_PORT = int(os.environ.get("TRIPLET_REVIEW_PORT", "8765"))

SERVICES = {
    "ontology": {
        "name": "本体抽取",
        "script": ROOT / "ontology_build_app.py",
        "port": ONTOLOGY_BUILD_PORT,
        "env_port": "ONTOLOGY_BUILD_PORT",
    },
    "review": {
        "name": "三元组审核",
        "script": ROOT / "review_triplets_app.py",
        "port": TRIPLET_REVIEW_PORT,
        "env_port": "TRIPLET_REVIEW_PORT",
    },
}

child_processes = {}
triplet_job = None


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel_path(path):
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def safe_root_path(value, default=None):
    if not value:
        if default is None:
            raise ValueError("缺少路径")
        value = default
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    root = ROOT.resolve()
    if path != root and root not in path.parents:
        raise ValueError("路径必须位于当前项目目录内")
    return path


def list_markdown_files():
    if not MDFILES_DIR.exists():
        return []
    return sorted(rel_path(path) for path in MDFILES_DIR.rglob("*.md"))


def port_open(port):
    try:
        with socket.create_connection((WORKBENCH_HOST, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def service_url(port):
    return f"http://{WORKBENCH_HOST}:{port}"


def service_status(service_id):
    spec = SERVICES[service_id]
    process = child_processes.get(service_id)
    running = port_open(spec["port"]) or (process is not None and process.poll() is None)
    return {
        "id": service_id,
        "name": spec["name"],
        "running": running,
        "port": spec["port"],
        "url": service_url(spec["port"]),
        "pid": process.pid if process is not None and process.poll() is None else None,
    }


def start_service(service_id):
    if service_id not in SERVICES:
        raise ValueError("未知服务")
    spec = SERVICES[service_id]
    if port_open(spec["port"]):
        return service_status(service_id)
    process = child_processes.get(service_id)
    if process is not None and process.poll() is None:
        return service_status(service_id)

    WORKBENCH_RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WORKBENCH_RUN_DIR / f"{service_id}_{now_stamp()}.log"
    env = os.environ.copy()
    env[spec["env_port"]] = str(spec["port"])
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        child_processes[service_id] = subprocess.Popen(
            [sys.executable, "-u", str(spec["script"])],
            cwd=str(ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    status = service_status(service_id)
    status["log_path"] = rel_path(log_path)
    return status


def stop_children():
    for process in child_processes.values():
        if process is not None and process.poll() is None:
            process.terminate()


atexit.register(stop_children)


def sanitize_stem(value):
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return text or "triplets"


def default_stem(markdown_path):
    stem = Path(markdown_path).stem
    if stem.startswith("MinerU_markdown_"):
        stem = stem[len("MinerU_markdown_"):]
    return sanitize_stem(stem)


def json_count(path):
    path = Path(path)
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return sum(len(value) for value in data.values() if isinstance(value, list))
    return 0


def start_triplet_extraction(payload):
    global triplet_job
    if triplet_job and triplet_job["process"].poll() is None:
        return triplet_status()

    markdown_path = safe_root_path(
        payload.get("markdown_path"),
        "mdfiles/MinerU_markdown_Ballistics.md",
    )
    if not markdown_path.exists() or markdown_path.suffix.lower() != ".md":
        raise ValueError("请选择项目内存在的 Markdown 文件")

    output_stem = sanitize_stem(payload.get("output_stem") or default_stem(markdown_path))
    output_json = ROOT / "triplets" / f"{output_stem}.json"
    evidence_md = ROOT / "evidence" / f"{output_stem}_evidence.md"
    rejected_json = ROOT / "rejected_triplets" / f"{output_stem}_rejected_triplets.json"

    output_json.parent.mkdir(parents=True, exist_ok=True)
    evidence_md.parent.mkdir(parents=True, exist_ok=True)
    rejected_json.parent.mkdir(parents=True, exist_ok=True)
    WORKBENCH_RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WORKBENCH_RUN_DIR / f"triplet_extract_{output_stem}_{now_stamp()}.log"

    env = os.environ.copy()
    env["TRIPLET_MARKDOWN_FILE"] = str(markdown_path)
    env["TRIPLET_OUTPUT_JSON"] = str(output_json)
    env["TRIPLET_EVIDENCE_MD"] = str(evidence_md)
    env["TRIPLET_REJECTED_JSON"] = str(rejected_json)
    env["TRIPLET_BOOK_NAME"] = output_stem

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "extract_triplets" / "extrct.py")],
            cwd=str(ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

    triplet_job = {
        "process": process,
        "started_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "markdown_path": markdown_path,
        "output_stem": output_stem,
        "output_json": output_json,
        "evidence_md": evidence_md,
        "rejected_json": rejected_json,
        "log_path": log_path,
    }
    return triplet_status()


def read_log_tail(path, limit=60000):
    path = Path(path)
    if not path.exists():
        return ""
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(max(0, size - limit), os.SEEK_SET)
        return file.read().decode("utf-8", errors="replace")


def triplet_status():
    if not triplet_job:
        return {"running": False, "exists": False}
    process = triplet_job["process"]
    returncode = process.poll()
    running = returncode is None
    return {
        "exists": True,
        "running": running,
        "pid": process.pid,
        "returncode": returncode,
        "started_at": triplet_job["started_at"],
        "markdown_path": rel_path(triplet_job["markdown_path"]),
        "output_stem": triplet_job["output_stem"],
        "outputs": {
            "accepted": rel_path(triplet_job["output_json"]),
            "rejected": rel_path(triplet_job["rejected_json"]),
            "evidence": rel_path(triplet_job["evidence_md"]),
            "log": rel_path(triplet_job["log_path"]),
        },
        "counts": {
            "accepted": json_count(triplet_job["output_json"]),
            "rejected": json_count(triplet_job["rejected_json"]),
        },
        "log_tail": read_log_tail(triplet_job["log_path"]),
    }


def html_page():
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>知识图谱工作台</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #d8dee7;
      --text: #1f2933;
      --muted: #657282;
      --accent: #1769aa;
      --accent-soft: #e8f2fb;
      --ok: #177245;
      --warn: #8a6100;
      --bad: #aa2e25;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    .shell {
      display: grid;
      grid-template-columns: 232px minmax(0, 1fr);
      min-height: 100vh;
    }
    aside {
      background: #182333;
      color: #eef4fb;
      padding: 18px 14px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .brand {
      padding: 4px 8px 12px;
      border-bottom: 1px solid rgba(255,255,255,.14);
    }
    .brand h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .nav {
      display: grid;
      gap: 8px;
    }
    .nav button {
      width: 100%;
      min-height: 42px;
      padding: 0 12px;
      border: 1px solid transparent;
      border-radius: 7px;
      color: #dce8f4;
      background: transparent;
      text-align: left;
      font: inherit;
      cursor: pointer;
    }
    .nav button.active {
      color: #ffffff;
      background: #245071;
      border-color: rgba(255,255,255,.16);
    }
    .service-stack {
      margin-top: auto;
      display: grid;
      gap: 8px;
    }
    .service {
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 7px;
      padding: 9px;
      color: #d6e2ef;
      font-size: 12px;
      display: grid;
      gap: 7px;
    }
    .service button {
      height: 30px;
      border-radius: 6px;
      border: 0;
      background: #eef4fb;
      color: #182333;
      cursor: pointer;
    }
    main {
      min-width: 0;
      display: grid;
      grid-template-rows: 58px minmax(0, 1fr);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    header h2 {
      margin: 0;
      font-size: 18px;
      font-weight: 680;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    a.button, button.primary, button.ghost {
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 0 12px;
      border-radius: 6px;
      font: inherit;
      text-decoration: none;
      cursor: pointer;
    }
    a.button, button.ghost {
      color: var(--text);
      background: #fff;
      border: 1px solid var(--line);
    }
    button.primary {
      color: #fff;
      background: var(--accent);
      border: 1px solid var(--accent);
    }
    .view {
      min-height: 0;
      padding: 14px;
      display: none;
    }
    .view.active {
      display: grid;
      gap: 12px;
      grid-template-rows: auto minmax(0, 1fr);
    }
    .frame-wrap {
      min-height: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }
    iframe {
      width: 100%;
      height: 100%;
      min-height: calc(100vh - 100px);
      border: 0;
      display: block;
      background: #fff;
    }
    .extract-grid {
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      gap: 12px;
      min-height: 0;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
    }
    .form {
      display: grid;
      gap: 12px;
      align-content: start;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    select, input {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 9px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      background: #fbfcfe;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .metric strong {
      display: block;
      margin-top: 4px;
      font-size: 22px;
      line-height: 1.1;
    }
    .status-line {
      min-height: 34px;
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--muted);
    }
    .dot.running { background: var(--accent); }
    .dot.done { background: var(--ok); }
    .dot.failed { background: var(--bad); }
    pre {
      height: 100%;
      min-height: 420px;
      margin: 0;
      padding: 12px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #111827;
      color: #dbeafe;
      font: 12px/1.6 Consolas, "Courier New", monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .paths {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .paths code {
      color: var(--text);
      background: #f0f3f7;
      border-radius: 4px;
      padding: 1px 4px;
    }
    @media (max-width: 880px) {
      .shell { grid-template-columns: 1fr; }
      aside { min-height: auto; }
      .service-stack { margin-top: 0; }
      .extract-grid { grid-template-columns: 1fr; }
      iframe { min-height: calc(100vh - 230px); }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand"><h1>知识图谱工作台</h1></div>
      <nav class="nav">
        <button data-view="ontology" onclick="activate('ontology')">本体抽取</button>
        <button data-view="triplet-extract" onclick="activate('triplet-extract')">三元组抽取</button>
        <button data-view="triplet-review" onclick="activate('triplet-review')">三元组审核</button>
      </nav>
      <div class="service-stack" id="serviceStack"></div>
    </aside>
    <main>
      <header>
        <h2 id="pageTitle">本体抽取</h2>
        <div class="header-actions" id="headerActions"></div>
      </header>

      <section id="ontology" class="view">
        <div class="status-line"><span class="dot" id="ontologyDot"></span><span id="ontologyStatus">检查中</span></div>
        <div class="frame-wrap"><iframe id="ontologyFrame" title="本体抽取"></iframe></div>
      </section>

      <section id="triplet-extract" class="view">
        <div class="status-line"><span class="dot" id="extractDot"></span><span id="extractStatus">空闲</span></div>
        <div class="extract-grid">
          <div class="panel form">
            <label>Markdown 文件<select id="markdownSelect" onchange="syncOutputStem()"></select></label>
            <label>输出名称<input id="outputStem"></label>
            <button class="primary" onclick="startTripletExtraction()">开始抽取</button>
            <button class="ghost" onclick="refreshTripletStatus()">刷新状态</button>
            <div class="metric-row">
              <div class="metric"><span>已接收</span><strong id="acceptedCount">0</strong></div>
              <div class="metric"><span>待审核</span><strong id="rejectedCount">0</strong></div>
              <div class="metric"><span>进程</span><strong id="extractPid">-</strong></div>
            </div>
            <div class="paths" id="outputPaths"></div>
          </div>
          <pre id="extractLog">等待抽取任务</pre>
        </div>
      </section>

      <section id="triplet-review" class="view">
        <div class="status-line"><span class="dot" id="reviewDot"></span><span id="reviewStatus">检查中</span></div>
        <div class="frame-wrap"><iframe id="reviewFrame" title="三元组审核"></iframe></div>
      </section>
    </main>
  </div>

<script>
const titles = {
  ontology: "本体抽取",
  "triplet-extract": "三元组抽取",
  "triplet-review": "三元组审核"
};
let state = {
  active: "ontology",
  markdownFiles: [],
  services: {},
  triplet: {}
};

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

function activate(view) {
  state.active = view;
  history.replaceState(null, "", view === "ontology" ? "/" : `/${view}`);
  document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === view));
  document.querySelectorAll(".nav button").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  document.getElementById("pageTitle").textContent = titles[view] || "";
  renderHeaderActions();
  if (view === "ontology") ensureFrame("ontology");
  if (view === "triplet-review") ensureFrame("review");
}

function renderHeaderActions() {
  const box = document.getElementById("headerActions");
  if (state.active === "ontology") {
    box.innerHTML = `<button class="ghost" onclick="startService('ontology')">启动页面</button><a class="button" target="_blank" href="${serviceUrl("ontology")}">新窗口</a>`;
  } else if (state.active === "triplet-review") {
    box.innerHTML = `<button class="ghost" onclick="startService('review')">启动页面</button><a class="button" target="_blank" href="${serviceUrl("review")}">新窗口</a>`;
  } else {
    box.innerHTML = `<button class="ghost" onclick="refreshTripletStatus()">刷新</button><a class="button" onclick="activate('triplet-review')">去审核</a>`;
  }
}

function serviceUrl(id) {
  return state.services[id]?.url || "#";
}

function ensureFrame(id) {
  const service = state.services[id];
  if (!service || !service.running) return;
  const frame = document.getElementById(id === "ontology" ? "ontologyFrame" : "reviewFrame");
  if (!frame.src) frame.src = service.url;
}

async function startService(id) {
  const data = await api("/api/services/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({service: id})
  });
  state.services[id] = data;
  renderServices();
  ensureFrame(id);
}

async function refreshServices() {
  const data = await api("/api/services");
  state.services = data.services;
  renderServices();
  ensureFrame("ontology");
  ensureFrame("review");
}

function renderServices() {
  document.getElementById("serviceStack").innerHTML = ["ontology", "review"].map(id => {
    const item = state.services[id] || {};
    const text = item.running ? `运行中 :${item.port}` : `未运行 :${item.port}`;
    return `<div class="service"><strong>${escapeHtml(item.name || id)}</strong><span>${escapeHtml(text)}</span><button onclick="startService('${id}')">启动</button></div>`;
  }).join("");
  setServiceStatus("ontology");
  setServiceStatus("review");
}

function setServiceStatus(id) {
  const item = state.services[id] || {};
  const dot = document.getElementById(id === "ontology" ? "ontologyDot" : "reviewDot");
  const label = document.getElementById(id === "ontology" ? "ontologyStatus" : "reviewStatus");
  if (!dot || !label) return;
  dot.className = `dot ${item.running ? "done" : "failed"}`;
  label.textContent = item.running ? `${item.name}页面运行中：${item.url}` : `${item.name || id}页面未运行`;
}

function syncOutputStem() {
  const select = document.getElementById("markdownSelect");
  const value = select.value || "";
  let stem = value.split("/").pop().replace(/\.md$/i, "");
  if (stem.startsWith("MinerU_markdown_")) stem = stem.slice("MinerU_markdown_".length);
  document.getElementById("outputStem").value = stem || "triplets";
}

async function startTripletExtraction() {
  setExtractStatus({running: true, log_tail: "任务启动中"});
  const body = {
    markdown_path: document.getElementById("markdownSelect").value,
    output_stem: document.getElementById("outputStem").value
  };
  state.triplet = await api("/api/triplet/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  renderTripletStatus();
}

async function refreshTripletStatus() {
  state.triplet = await api("/api/triplet/status");
  renderTripletStatus();
}

function setExtractStatus(job) {
  const dot = document.getElementById("extractDot");
  const label = document.getElementById("extractStatus");
  dot.className = `dot ${job.running ? "running" : job.returncode === 0 ? "done" : job.exists ? "failed" : ""}`;
  if (job.running) label.textContent = "抽取运行中";
  else if (!job.exists) label.textContent = "空闲";
  else if (job.returncode === 0) label.textContent = "抽取完成";
  else label.textContent = `抽取结束，退出码 ${job.returncode}`;
}

function renderTripletStatus() {
  const job = state.triplet || {};
  setExtractStatus(job);
  document.getElementById("extractPid").textContent = job.pid || "-";
  document.getElementById("acceptedCount").textContent = job.counts?.accepted ?? 0;
  document.getElementById("rejectedCount").textContent = job.counts?.rejected ?? 0;
  document.getElementById("extractLog").textContent = job.log_tail || "等待抽取任务";
  const outputs = job.outputs || {};
  document.getElementById("outputPaths").innerHTML = Object.entries(outputs)
    .map(([name, path]) => `<div>${escapeHtml(name)} <code>${escapeHtml(path)}</code></div>`)
    .join("");
}

async function init() {
  const data = await api("/api/init");
  state.markdownFiles = data.markdown_files || [];
  state.services = data.services || {};
  document.getElementById("markdownSelect").innerHTML = state.markdownFiles
    .map(path => `<option value="${escapeAttr(path)}">${escapeHtml(path)}</option>`)
    .join("");
  syncOutputStem();
  renderServices();
  await refreshTripletStatus();
  const pathView = location.pathname.replace("/", "") || "ontology";
  activate(titles[pathView] ? pathView : "ontology");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

setInterval(() => {
  refreshServices().catch(() => {});
  refreshTripletStatus().catch(() => {});
}, 4000);

init().catch(err => {
  document.getElementById("extractLog").textContent = err.message;
});
</script>
</body>
</html>"""


class WorkbenchHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self):
        body = html_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_payload(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/ontology", "/triplet-extract", "/triplet-review"}:
                self.send_html()
            elif parsed.path == "/api/init":
                self.send_json({
                    "markdown_files": list_markdown_files(),
                    "services": {key: service_status(key) for key in SERVICES},
                })
            elif parsed.path == "/api/services":
                self.send_json({"services": {key: service_status(key) for key in SERVICES}})
            elif parsed.path == "/api/triplet/status":
                self.send_json(triplet_status())
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_payload()
            if parsed.path == "/api/services/start":
                self.send_json(start_service(payload.get("service", "")))
            elif parsed.path == "/api/triplet/start":
                self.send_json(start_triplet_extraction(payload))
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt, *args):
        return


def main():
    for service_id in SERVICES:
        try:
            start_service(service_id)
        except Exception as exc:
            print(f"{SERVICES[service_id]['name']}启动失败: {exc}")
    server = ThreadingHTTPServer((WORKBENCH_HOST, WORKBENCH_PORT), WorkbenchHandler)
    print(f"知识图谱工作台已启动: http://{WORKBENCH_HOST}:{WORKBENCH_PORT}")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()


if __name__ == "__main__":
    main()
