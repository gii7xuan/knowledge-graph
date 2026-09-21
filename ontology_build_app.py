import copy
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ontologyBuild.review_module import apply_user_decision, review_candidates


ROOT = Path(__file__).resolve().parent
ONTOLOGY_BUILD_DIR = ROOT / "ontologyBuild"
MDFILES_DIR = ROOT / "mdfiles"
DEFAULT_CANDIDATES_PATH = ONTOLOGY_BUILD_DIR / "ontology_candidates.json"
DEFAULT_MODULES_PATH = ONTOLOGY_BUILD_DIR / "ontology_modules.json"
DEFAULT_REVIEW_RESULT_PATH = ONTOLOGY_BUILD_DIR / "ontology_review_result.json"
DEFAULT_REVIEW_LOG_PATH = ONTOLOGY_BUILD_DIR / "ontology_review_log.json"

RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)

SECTION_TYPE = {
    "candidate_classes": "class",
    "candidate_object_properties": "object_property",
    "candidate_datatype_properties": "datatype_property",
    "candidate_constraints": "constraint",
    "ontology_mapping": "mapping",
    "uncertain_candidates": "uncertain",
}


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def list_markdown_files():
    if not MDFILES_DIR.exists():
        return []
    return sorted(str(path.relative_to(ROOT)).replace("\\", "/") for path in MDFILES_DIR.glob("*.md"))


def safe_root_path(value, default_path):
    if not value:
        return Path(default_path)
    path = (ROOT / value).resolve()
    if ROOT.resolve() not in [path, *path.parents]:
        raise ValueError("路径必须位于当前项目目录内")
    return path


def load_candidate_source(path):
    data = read_json(path, None)
    if data is None:
        raise FileNotFoundError(f"找不到候选本体文件: {path}")
    return data


def confidence_of(item):
    for key in ("confidence", "mapping_confidence"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return 0.0


def display_name(item):
    if item.get("name"):
        return str(item["name"])
    if item.get("property_name"):
        name = str(item["property_name"])
        domain = item.get("domain_candidate")
        range_value = item.get("range_candidate") or item.get("range_datatype")
        if domain or range_value:
            return f"{name}: {domain or '?'} -> {range_value or '?'}"
        return name
    if item.get("text_mention"):
        return f"{item.get('text_mention')} -> {item.get('mapped_to')}"
    return str(item.get("candidate_id") or "未命名候选")


def flatten_candidates(data):
    if isinstance(data, list):
        result = []
        for index, item in enumerate(data):
            if isinstance(item, dict):
                if isinstance(item.get("candidate"), dict):
                    candidate = copy.deepcopy(item["candidate"])
                    candidate.setdefault("source_section", item.get("source_section"))
                    candidate.setdefault("review_type", item.get("review_type"))
                    candidate.setdefault("reason", item.get("reason"))
                else:
                    candidate = copy.deepcopy(item)
                candidate.setdefault("candidate_type", candidate.get("candidate_type") or "manual_review")
                candidate.setdefault("candidate_id", f"manual_{index + 1:06d}")
                result.append(candidate)
        return result

    if not isinstance(data, dict):
        raise ValueError("候选本体数据必须是 JSON 对象或数组")

    result = []
    for section in RESULT_FIELDS:
        values = data.get(section, [])
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            candidate = copy.deepcopy(item)
            candidate["source_section"] = section
            candidate.setdefault("candidate_type", SECTION_TYPE.get(section, section))
            candidate.setdefault("candidate_id", f"{section}_{index + 1:06d}")
            result.append(candidate)
    return result


def enrich_candidate_for_response(candidate):
    item = copy.deepcopy(candidate)
    item["display_name"] = display_name(item)
    item["confidence_display"] = f"{confidence_of(item):.4f}"
    source_module = item.get("source_module")
    if isinstance(source_module, dict):
        item["source_module_text"] = source_module.get("module_name") or ""
        item["directory_context_text"] = source_module.get("breadcrumb_path") or ""
        item["directory_role_text"] = source_module.get("directory_role") or ""
    else:
        item["source_module_text"] = ""
        item["directory_context_text"] = ""
        item["directory_role_text"] = ""
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        item["evidence_text"] = "\n\n".join(
            str(piece.get("text", "")) for piece in evidence if isinstance(piece, dict)
        )
    else:
        item["evidence_text"] = ""
    return item


def get_review_result():
    return read_json(DEFAULT_REVIEW_RESULT_PATH, {
        "passed": True,
        "review_mode": "manual",
        "accepted_candidates": [],
        "rejected_candidates": [],
        "pending_review_candidates": [],
        "deferred_candidates": [],
        "review_log": [],
        "summary": {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "pending_review": 0,
            "deferred": 0,
        },
        "errors": [],
    })


def build_review_from_candidates(source_path, review_mode, reviewer):
    candidates = flatten_candidates(load_candidate_source(source_path))
    result = review_candidates(candidates, review_mode=review_mode, reviewer=reviewer)
    result["source_file"] = str(Path(source_path).relative_to(ROOT)).replace("\\", "/")
    result["generated_at"] = now_iso()
    write_json(DEFAULT_REVIEW_RESULT_PATH, result)
    return result


def run_extraction(markdown_path):
    env = os.environ.copy()
    env["ONTOLOGY_MARKDOWN_FILE"] = str(markdown_path)
    script_name = env.get("ONTOLOGY_EXTRACTION_SCRIPT", "directory_guided_builder.py")
    proc = subprocess.run(
        [sys.executable, str(ONTOLOGY_BUILD_DIR / script_name)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "本体候选抽取失败").strip())
    return {"stdout": proc.stdout, "stderr": proc.stderr}


def html_page():
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>本体构建与人工审核</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d8dee9;
      --text: #1f2937;
      --muted: #6b7280;
      --accent: #1769aa;
      --ok: #14743a;
      --bad: #a61b1b;
      --warn: #8a5a00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      height: 58px;
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    button, select, input, textarea { font: inherit; }
    select, input, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
    }
    select, input { height: 34px; padding: 0 9px; }
    button {
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      cursor: pointer;
    }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.ok { background: var(--ok); color: #fff; border-color: var(--ok); }
    button.bad { background: var(--bad); color: #fff; border-color: var(--bad); }
    button.warn { background: #fff8e6; color: var(--warn); border-color: #f0c36a; }
    main {
      display: grid;
      grid-template-columns: 340px 1fr;
      min-height: calc(100vh - 58px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 14px;
      overflow: auto;
    }
    .content { padding: 14px 18px 28px; overflow: auto; }
    .controls { display: grid; gap: 10px; }
    label { display: grid; gap: 5px; font-size: 12px; color: var(--muted); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .stats {
      display: grid;
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 8px;
      margin-bottom: 14px;
    }
    .stat, .card, .log {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .stat { padding: 10px; }
    .stat strong { display: block; font-size: 20px; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 12px;
    }
    .card {
      padding: 14px;
      margin-bottom: 10px;
    }
    .title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .title strong { font-size: 16px; }
    .badge {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
      white-space: nowrap;
    }
    .meta, .reason { color: var(--muted); font-size: 13px; line-height: 1.6; }
    .confidence { color: var(--accent); font-weight: 650; }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 10px; }
    textarea { width: 100%; min-height: 58px; padding: 8px; resize: vertical; margin-top: 8px; }
    .evidence {
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      padding: 10px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.7;
      max-height: 260px;
      overflow: auto;
      font-size: 13px;
    }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .empty { color: var(--muted); padding: 32px; text-align: center; }
    .log { padding: 10px; white-space: pre-wrap; max-height: 220px; overflow: auto; font-size: 12px; color: var(--muted); }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .stats, .grid, .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>本体构建与人工审核</h1>
    <button class="primary" onclick="refresh()">刷新</button>
  </header>
  <main>
    <aside>
      <div class="controls">
        <label>Markdown 文档<select id="markdownSelect"></select></label>
        <label>候选本体 JSON<input id="sourcePath" value="ontologyBuild/ontology_candidates.json"></label>
        <div class="row">
          <label>审核模式<select id="reviewMode"><option value="manual">人工审核</option><option value="hybrid">混合审核</option><option value="auto">自动审核</option></select></label>
          <label>审核人<input id="reviewer" value="human"></label>
        </div>
        <button class="primary" onclick="runReview()">生成审核队列</button>
        <button onclick="runExtractionAndReview()">抽取并进入最后审核</button>
        <div class="meta">选择“人工审核”时，所有候选本体元素都会进入待审队列；选择“混合审核”时，只展示系统判定需要人工处理的候选。</div>
        <div id="runLog" class="log">等待操作</div>
      </div>
    </aside>
    <section class="content">
      <div id="stats" class="stats"></div>
      <div class="toolbar">
        <select id="statusFilter" onchange="render()">
          <option value="pending_review_candidates">待人工审核</option>
          <option value="accepted_candidates">已通过</option>
          <option value="rejected_candidates">已拒绝</option>
          <option value="deferred_candidates">已暂缓</option>
        </select>
        <input id="keyword" placeholder="搜索名称、类型、证据" oninput="render()">
      </div>
      <div id="cards"></div>
    </section>
  </main>
<script>
let state = { result: null, markdownFiles: [] };

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function init() {
  const info = await api("/api/init");
  state.markdownFiles = info.markdown_files;
  document.getElementById("markdownSelect").innerHTML = state.markdownFiles
    .map(path => `<option value="${escapeAttr(path)}">${escapeHtml(path)}</option>`).join("");
  await refresh();
}

async function refresh() {
  state.result = await api("/api/review-result");
  render();
}

async function runReview() {
  setLog("正在生成审核队列...");
  const body = {
    source_path: document.getElementById("sourcePath").value,
    review_mode: document.getElementById("reviewMode").value,
    reviewer: document.getElementById("reviewer").value
  };
  state.result = await api("/api/review-run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  setLog("审核队列已生成。");
  document.getElementById("statusFilter").value = "pending_review_candidates";
  render();
}

async function runExtractionAndReview() {
  setLog("正在抽取候选本体元素，可能需要较长时间...");
  const body = {
    markdown_path: document.getElementById("markdownSelect").value,
    review_mode: document.getElementById("reviewMode").value,
    reviewer: document.getElementById("reviewer").value
  };
  state.result = await api("/api/build-run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  setLog("抽取完成，并已进入最后审核。");
  document.getElementById("sourcePath").value = "ontologyBuild/ontology_candidates.json";
  document.getElementById("statusFilter").value = "pending_review_candidates";
  render();
}

function render() {
  const result = state.result || {};
  const summary = result.summary || {};
  const stats = [
    ["总数", summary.total || 0],
    ["待审", summary.pending_review || 0],
    ["通过", summary.accepted || 0],
    ["拒绝", summary.rejected || 0],
    ["暂缓", summary.deferred || 0],
  ];
  document.getElementById("stats").innerHTML = stats
    .map(([k, v]) => `<div class="stat"><span>${k}</span><strong>${v}</strong></div>`).join("");

  const listName = document.getElementById("statusFilter").value;
  const keyword = document.getElementById("keyword").value.trim().toLowerCase();
  let items = result[listName] || [];
  if (keyword) {
    items = items.filter(item => JSON.stringify(item).toLowerCase().includes(keyword));
  }
  const cards = document.getElementById("cards");
  if (!items.length) {
    cards.innerHTML = `<div class="empty">当前没有需要展示的本体候选项</div>`;
    return;
  }
  cards.innerHTML = items.map(renderCard).join("");
}

function renderCard(item) {
  const id = item.candidate_id || "";
  const evidence = item.evidence_text || "";
  const moduleLine = item.source_module_text
    ? `<div class="meta">子本体模块 <strong>${escapeHtml(item.source_module_text)}</strong> · 目录路径 ${escapeHtml(item.directory_context_text || "")}</div>`
    : "";
  return `<article class="card" id="card-${escapeAttr(id)}">
    <div class="title">
      <strong>${escapeHtml(item.display_name || item.name || id)}</strong>
      <span class="badge">${escapeHtml(item.candidate_type || "")}</span>
    </div>
    <div class="meta">置信度 <span class="confidence">${escapeHtml(item.confidence_display || "0.0000")}</span> · 状态 ${escapeHtml(item.final_decision || "")} · 推荐 ${escapeHtml(item.system_recommendation || "")}</div>
    ${moduleLine}
    <div class="reason">${escapeHtml((item.recommendation_reasons || []).join("；") || item.reason || item.why_uncertain || "")}</div>
    <div class="grid">
      <label>名称<input id="name-${escapeAttr(id)}" value="${escapeAttr(item.name || item.property_name || item.text_mention || "")}"></label>
      <label>上位/domain<input id="domain-${escapeAttr(id)}" value="${escapeAttr(item.parent_candidate || item.domain_candidate || "")}"></label>
      <label>range/映射<input id="range-${escapeAttr(id)}" value="${escapeAttr(item.range_candidate || item.range_datatype || item.mapped_to || "")}"></label>
    </div>
    <textarea id="comment-${escapeAttr(id)}" placeholder="审核备注"></textarea>
    <details>
      <summary>证据与完整 JSON</summary>
      <div class="evidence">${escapeHtml(evidence || "没有证据文本")}</div>
      <div class="evidence">${escapeHtml(JSON.stringify(item, null, 2))}</div>
    </details>
    <div class="actions">
      <button class="ok" onclick="decide(${jsArg(id)}, 'accept')">通过</button>
      <button class="primary" onclick="decide(${jsArg(id)}, 'modify')">修改后通过</button>
      <button class="bad" onclick="decide(${jsArg(id)}, 'reject')">拒绝</button>
      <button class="warn" onclick="decide(${jsArg(id)}, 'defer')">暂缓</button>
    </div>
  </article>`;
}

async function decide(id, decision) {
  const modifications = {
    name: document.getElementById(`name-${id}`).value,
    property_name: document.getElementById(`name-${id}`).value,
    domain_candidate: document.getElementById(`domain-${id}`).value,
    parent_candidate: document.getElementById(`domain-${id}`).value,
    range_candidate: document.getElementById(`range-${id}`).value,
    mapped_to: document.getElementById(`range-${id}`).value
  };
  state.result = await api("/api/review-decision", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      candidate_id: id,
      decision,
      reviewer: document.getElementById("reviewer").value,
      comment: document.getElementById(`comment-${id}`).value,
      modifications: decision === "modify" ? modifications : null
    })
  });
  render();
}

function setLog(message) {
  document.getElementById("runLog").textContent = message;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
function jsArg(value) {
  return `decodeURIComponent('${encodeURIComponent(String(value ?? ""))}')`;
}

init().catch(err => {
  setLog(err.message);
  document.getElementById("cards").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
});
</script>
</body>
</html>"""


class OntologyBuildHandler(BaseHTTPRequestHandler):
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
            if parsed.path == "/":
                self.send_html()
            elif parsed.path == "/api/init":
                self.send_json({
                    "markdown_files": list_markdown_files(),
                    "module_index": read_json(DEFAULT_MODULES_PATH, {}),
                })
            elif parsed.path == "/api/review-result":
                result = get_review_result()
                self.send_json(enrich_review_result(result))
            elif parsed.path == "/api/module-index":
                self.send_json(read_json(DEFAULT_MODULES_PATH, {}))
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_payload()
            if parsed.path == "/api/review-run":
                self.handle_review_run(payload)
            elif parsed.path == "/api/build-run":
                self.handle_build_run(payload)
            elif parsed.path == "/api/review-decision":
                self.handle_review_decision(payload)
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_review_run(self, payload):
        source_path = safe_root_path(payload.get("source_path"), DEFAULT_CANDIDATES_PATH)
        review_mode = payload.get("review_mode", "manual")
        reviewer = payload.get("reviewer", "human")
        if review_mode not in {"auto", "manual", "hybrid"}:
            raise ValueError("review_mode 必须是 auto/manual/hybrid")
        result = build_review_from_candidates(source_path, review_mode, reviewer)
        self.send_json(enrich_review_result(result))

    def handle_build_run(self, payload):
        markdown_path = safe_root_path(payload.get("markdown_path"), "")
        if not markdown_path.exists() or markdown_path.suffix.lower() != ".md":
            raise ValueError("请选择项目内存在的 Markdown 文档")
        review_mode = payload.get("review_mode", "manual")
        reviewer = payload.get("reviewer", "human")
        run_extraction(markdown_path)
        result = build_review_from_candidates(DEFAULT_CANDIDATES_PATH, review_mode, reviewer)
        self.send_json(enrich_review_result(result))

    def handle_review_decision(self, payload):
        result = get_review_result()
        updated = apply_user_decision(
            result,
            candidate_id=payload.get("candidate_id", ""),
            user_decision=payload.get("decision", ""),
            reviewer=payload.get("reviewer", "human"),
            review_comment=payload.get("comment"),
            modifications=payload.get("modifications"),
        )
        write_json(DEFAULT_REVIEW_RESULT_PATH, updated)
        write_json(DEFAULT_REVIEW_LOG_PATH, updated.get("review_log", []))
        self.send_json(enrich_review_result(updated))

    def log_message(self, fmt, *args):
        return


def enrich_review_result(result):
    enriched = copy.deepcopy(result)
    for list_name in (
        "accepted_candidates",
        "rejected_candidates",
        "pending_review_candidates",
        "deferred_candidates",
    ):
        enriched[list_name] = [
            enrich_candidate_for_response(item)
            for item in enriched.get(list_name, [])
            if isinstance(item, dict)
        ]
    return enriched


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("ONTOLOGY_BUILD_PORT", "8766"))
    server = ThreadingHTTPServer((host, port), OntologyBuildHandler)
    print(f"本体构建页面已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()


if __name__ == "__main__":
    main()
