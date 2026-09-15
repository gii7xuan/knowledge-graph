import copy
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
REJECTED_DIR = ROOT / "rejected_triplets"
TRIPLETS_DIR = ROOT / "triplets"
REVIEW_LOG_DIR = ROOT / "review_logs"
EVIDENCE_DIR = ROOT / "evidence"
RUNS_V2_ACCEPTED_DIR = ROOT / "runs" / "v2" / "accepted"
EVIDENCE_CACHE = {}


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path, default):
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def list_review_files():
    if not REJECTED_DIR.exists():
        return []
    return sorted(p.name for p in REJECTED_DIR.glob("*.json"))


def target_triplets_path(review_file):
    name = review_file
    suffix = "_rejected_triplets.json"
    if name.endswith(suffix):
        return TRIPLETS_DIR / f"{name[:-len(suffix)]}.json"
    return TRIPLETS_DIR / name


def evidence_path(review_file):
    name = review_file
    suffix = "_rejected_triplets.json"
    if name.endswith(suffix):
        return EVIDENCE_DIR / f"{name[:-len(suffix)]}_evidence.md"
    return EVIDENCE_DIR / f"{Path(name).stem}_evidence.md"


def parse_evidence_chunks(path):
    if not path.exists():
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    if cache_key in EVIDENCE_CACHE:
        return EVIDENCE_CACHE[cache_key]

    text = path.read_text(encoding="utf-8")
    chunks = {}
    pattern = re.compile(r"^## ID:\s*(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        source_id = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content.endswith("---"):
            content = content[:-3].strip()
        chunks[source_id] = content

    EVIDENCE_CACHE.clear()
    EVIDENCE_CACHE[cache_key] = chunks
    return chunks


def evidence_chunk_for(review_file, source_id):
    if not source_id:
        return ""
    chunks = parse_evidence_chunks(evidence_path(review_file))
    return chunks.get(source_id, "")


def item_id(item, index):
    return item.get("review_id") or f"{item.get('source_id', 'source')}::{index}"


def confidence_value(item):
    value = item.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        matched = re.search(r"-?\d+(?:\.\d+)?", value)
        if matched:
            return float(matched.group(0))
    return 0.0


def item_for_response(review_file, item):
    result = copy.deepcopy(item)
    confidence = confidence_value(item)
    result["_confidence"] = confidence
    result["confidence_display"] = f"{confidence:.4f}"
    result["evidence_chunk"] = evidence_chunk_for(review_file, item.get("source_id", ""))
    return result


def item_relation(item):
    return item.get("corrected_relation") or item.get("relation") or "未命名关系"


def review_status(item):
    return item.get("review_status", "pending")


def load_review_items(review_file):
    path = REJECTED_DIR / review_file
    data = read_json(path, [])
    if not isinstance(data, list):
        raise ValueError("待审文件必须是 JSON 数组")
    changed = False
    for index, item in enumerate(data):
        if isinstance(item, dict) and not item.get("review_id"):
            item["review_id"] = item_id(item, index)
            changed = True
    if changed:
        write_json(path, data)
    return data


def normalize_triplet(item):
    result = copy.deepcopy(item)
    if result.get("corrected_relation"):
        result["original_relation"] = result.get("relation")
        result["relation"] = result["corrected_relation"]
    for key in ["discard_reason", "needs_review", "corrected_relation", "review_status"]:
        result.pop(key, None)
    result["review_decision"] = "accepted"
    result["decision"] = "human_accept"
    result["review_time"] = now_iso()
    return result


def append_unique_triplet(review_file, item):
    path = target_triplets_path(review_file)
    triplets = read_json(path, [])
    if not isinstance(triplets, list):
        raise ValueError(f"{path.name} 必须是 JSON 数组")

    candidate = normalize_triplet(item)
    legacy_written = append_unique_to_path(path, candidate)

    v2_path = RUNS_V2_ACCEPTED_DIR / target_triplets_path(review_file).name
    append_unique_to_path(v2_path, candidate)
    return legacy_written


def append_unique_to_path(path, candidate):
    triplets = read_json(path, [])
    if not isinstance(triplets, list):
        raise ValueError(f"{path.name} 必须是 JSON 数组")
    key = (
        candidate.get("head", ""),
        candidate.get("relation", ""),
        candidate.get("tail", ""),
        candidate.get("source_id", ""),
    )
    for existing in triplets:
        if (
            existing.get("head", ""),
            existing.get("relation", ""),
            existing.get("tail", ""),
            existing.get("source_id", ""),
        ) == key:
            return False
    triplets.append(candidate)
    write_json(path, triplets)
    return True


def append_review_log(review_file, log_item):
    path = REVIEW_LOG_DIR / f"{Path(review_file).stem}_review_log.json"
    logs = read_json(path, [])
    if not isinstance(logs, list):
        logs = []
    logs.append(log_item)
    write_json(path, logs)


def build_summary(review_file, min_confidence=None, max_confidence=None, include_done=False):
    items = load_review_items(review_file)
    groups = {}
    total = pending = accepted = rejected = deferred = 0
    filtered = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        total += 1
        status = review_status(item)
        if status == "accepted":
            accepted += 1
        elif status == "rejected":
            rejected += 1
        elif status == "deferred":
            deferred += 1
        else:
            pending += 1

        if not include_done and status in {"accepted", "rejected"}:
            continue
        conf = confidence_value(item)
        if min_confidence is not None and conf < min_confidence:
            continue
        if max_confidence is not None and conf > max_confidence:
            continue
        relation = item_relation(item)
        groups.setdefault(relation, 0)
        groups[relation] += 1
        filtered.append(index)

    return {
        "file": review_file,
        "total": total,
        "pending": pending,
        "accepted": accepted,
        "rejected": rejected,
        "deferred": deferred,
        "filtered": len(filtered),
        "groups": [{"relation": name, "count": count} for name, count in sorted(groups.items())],
    }


def parse_float_arg(query, name):
    value = query.get(name, [""])[0].strip()
    if not value:
        return None
    return float(value)


def html_page():
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>三元组人工审核</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #20242c;
      --muted: #6b7280;
      --accent: #1668dc;
      --ok: #16803c;
      --bad: #b42318;
      --warn: #946200;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    header h1 {
      font-size: 18px;
      margin: 0;
      font-weight: 650;
    }
    select, input, textarea, button {
      font: inherit;
    }
    select, input, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
    }
    select, input {
      height: 34px;
      padding: 0 9px;
    }
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
    button.warn { background: #fff7e6; color: var(--warn); border-color: #ffd591; }
    main {
      display: grid;
      grid-template-columns: 300px 1fr;
      min-height: calc(100vh - 56px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 14px;
      overflow: auto;
    }
    .content {
      padding: 14px 18px 28px;
      overflow: auto;
    }
    .filters {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
    }
    label {
      display: grid;
      gap: 5px;
      font-size: 12px;
      color: var(--muted);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(6, minmax(90px, 1fr));
      gap: 8px;
      margin-bottom: 14px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }
    .stat strong {
      display: block;
      font-size: 20px;
    }
    .relation {
      width: 100%;
      height: auto;
      min-height: 36px;
      padding: 8px;
      margin-bottom: 6px;
      text-align: left;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .relation.active {
      border-color: var(--accent);
      color: var(--accent);
      background: #eef5ff;
    }
    .badge {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 10px;
    }
    .triple {
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }
    .node, .rel {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: #fbfcfe;
      min-height: 38px;
    }
    .rel {
      color: var(--accent);
      white-space: nowrap;
    }
    .meta, .reason {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    .confidence {
      color: var(--accent);
      font-weight: 650;
    }
    .evidence-box {
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      padding: 10px;
      word-break: break-word;
      line-height: 1.7;
      max-height: 360px;
      overflow: auto;
      font-size: 13px;
    }
    .evidence-box h1,
    .evidence-box h2,
    .evidence-box h3 {
      margin: 10px 0 6px;
      line-height: 1.35;
    }
    .evidence-box h1 { font-size: 18px; }
    .evidence-box h2 { font-size: 16px; }
    .evidence-box h3 { font-size: 14px; }
    .evidence-box p {
      margin: 6px 0;
    }
    .evidence-box ul,
    .evidence-box ol {
      margin: 6px 0 6px 22px;
      padding: 0;
    }
    .evidence-box blockquote {
      margin: 8px 0;
      padding: 2px 10px;
      border-left: 3px solid var(--line);
      color: var(--muted);
      background: #fff;
    }
    .evidence-box code {
      padding: 1px 4px;
      border-radius: 4px;
      background: #eef1f5;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
    }
    .evidence-box pre {
      margin: 8px 0;
      padding: 10px;
      overflow: auto;
      border-radius: 6px;
      background: #eef1f5;
    }
    .evidence-box pre code {
      padding: 0;
      background: transparent;
    }
    .evidence-box .math-display {
      overflow-x: auto;
      margin: 8px 0;
      padding: 4px 0;
    }
    details {
      margin-top: 8px;
    }
    summary {
      cursor: pointer;
      color: var(--muted);
    }
    .edit {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 8px;
      margin-top: 10px;
    }
    textarea {
      width: 100%;
      min-height: 56px;
      padding: 8px;
      resize: vertical;
      margin-top: 8px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .empty {
      color: var(--muted);
      padding: 30px;
      text-align: center;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 280px; }
      .filters, .stats, .edit { grid-template-columns: 1fr 1fr; }
      .triple { grid-template-columns: 1fr; }
      .rel { white-space: normal; }
    }
  </style>
  <script>
    window.mathJaxLoadFailed = false;
    window.mathJaxReady = new Promise(resolve => {
      window.resolveMathJaxReady = resolve;
    });
    window.MathJax = {
      tex: {
        inlineMath: [["$", "$"], ["\\(", "\\)"]],
        displayMath: [["$$", "$$"], ["\\[", "\\]"]],
        processEscapes: true
      },
      options: {
        skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code"]
      },
      startup: {
        ready: () => {
          window.MathJax.startup.defaultReady();
          window.resolveMathJaxReady();
          if (window.onMathJaxReady) window.onMathJaxReady();
        }
      }
    };
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" onerror="window.mathJaxLoadFailed = true; window.resolveMathJaxReady();"></script>
</head>
<body>
  <header>
    <h1>三元组人工审核</h1>
    <select id="fileSelect"></select>
    <button class="primary" onclick="loadAll()">刷新</button>
  </header>
  <main>
    <aside>
      <div class="filters">
        <label>最低置信度<input id="minConfidence" type="number" step="0.01" min="0" max="1" placeholder="如 0.4"></label>
        <label>最高置信度<input id="maxConfidence" type="number" step="0.01" min="0" max="1" placeholder="如 0.6"></label>
        <label>状态<select id="includeDone"><option value="0">待处理</option><option value="1">全部</option></select></label>
        <button class="primary" onclick="loadAll()">筛选</button>
      </div>
      <div id="relations"></div>
    </aside>
    <section class="content">
      <div id="stats" class="stats"></div>
      <div id="cards"></div>
    </section>
  </main>
<script>
let state = { file: "", relation: "" };
let mathTypesetQueue = Promise.resolve();

window.onMathJaxReady = () => {
  const cards = document.getElementById("cards");
  if (cards && cards.children.length) typesetMath(cards);
};

function qs(params) {
  return new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

function filters() {
  return {
    file: state.file,
    relation: state.relation,
    min_confidence: document.getElementById("minConfidence").value,
    max_confidence: document.getElementById("maxConfidence").value,
    include_done: document.getElementById("includeDone").value
  };
}

async function init() {
  const files = await api("/api/files");
  const select = document.getElementById("fileSelect");
  select.innerHTML = files.map(f => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("");
  select.onchange = () => { state.file = select.value; state.relation = ""; loadAll(); };
  state.file = files[0] || "";
  await loadAll();
}

async function loadAll() {
  if (!state.file) {
    document.getElementById("cards").innerHTML = `<div class="empty">没有找到待审文件</div>`;
    return;
  }
  document.getElementById("fileSelect").value = state.file;
  const summary = await api("/api/summary?" + qs(filters()));
  renderSummary(summary);
  await loadItems();
}

function renderSummary(summary) {
  const stats = [
    ["总数", summary.total],
    ["待处理", summary.pending],
    ["已通过", summary.accepted],
    ["已拒绝", summary.rejected],
    ["暂缓", summary.deferred],
    ["筛选后", summary.filtered],
  ];
  document.getElementById("stats").innerHTML = stats.map(([k, v]) => `<div class="stat"><span>${k}</span><strong>${v}</strong></div>`).join("");
  const rels = [`<button class="relation ${state.relation === "" ? "active" : ""}" onclick="chooseRelation('')"><span>全部关系</span><span class="badge">${summary.filtered}</span></button>`]
    .concat(summary.groups.map(g => `<button class="relation ${state.relation === g.relation ? "active" : ""}" onclick="chooseRelation(${jsArg(g.relation)})"><span>${escapeHtml(g.relation)}</span><span class="badge">${g.count}</span></button>`));
  document.getElementById("relations").innerHTML = rels.join("");
}

function chooseRelation(relation) {
  state.relation = relation;
  loadAll();
}

async function loadItems() {
  const data = await api("/api/items?" + qs(filters()));
  const cards = document.getElementById("cards");
  if (!data.items.length) {
    cards.innerHTML = `<div class="empty">当前筛选条件下没有待审三元组</div>`;
    return;
  }
  cards.innerHTML = data.items.map(renderCard).join("");
  typesetMath(cards);
}

function renderCard(item) {
  const relation = item.corrected_relation || item.relation || "";
  const status = item.review_status || "pending";
  const confidence = item.confidence_display ?? confidenceDisplay(item.confidence);
  const rawConfidence = item.confidence_detail?.raw_confidence || item.confidence_detail || {};
  const bestEvidence = item.confidence_detail?.evidence_result?.best_evidence || {};
  const typeValidation = item.type_validation || item.confidence_detail?.type_validation || {};
  return `<article class="card" id="card-${escapeAttr(item.review_id)}">
    <div class="triple">
      <div class="node">${escapeHtml(item.head || "")}<div class="meta">${escapeHtml(item.head_type || "Unknown")}</div></div>
      <div class="rel">${escapeHtml(relation)}</div>
      <div class="node">${escapeHtml(item.tail || "")}<div class="meta">${escapeHtml(item.tail_type || "Unknown")}</div></div>
    </div>
    <div class="meta">置信度 <span class="confidence">${escapeHtml(confidence)}</span> · 状态 ${escapeHtml(status)} · 来源 ${escapeHtml(item.source_id || "")}</div>
    ${item.corrected_relation ? `<div class="reason">原关系：${escapeHtml(item.relation || "")}，建议关系：${escapeHtml(item.corrected_relation || "")}</div>` : ""}
    <div class="reason">${escapeHtml(item.discard_reason || item.confidence_decision?.reason || "")}</div>
    <details>
      <summary>查看评分细节和证据</summary>
      <div class="reason">${escapeHtml(rawConfidence.logic_info || "")}</div>
      <div class="reason">${escapeHtml(rawConfidence.map_info || "")}</div>
      <div class="reason">${escapeHtml(rawConfidence.evidence_info || "")}</div>
      <div class="reason">domain/range：${escapeHtml(typeValidation.passed === false ? (typeValidation.errors || []).join("；") : "通过")}</div>
      ${item.evidence || bestEvidence.text ? `<div class="reason">证据片段：${escapeHtml(item.evidence || bestEvidence.text)}</div>` : ""}
      ${bestEvidence.level ? `<div class="reason">证据层级：${escapeHtml(bestEvidence.level)} · 位置 ${escapeHtml(bestEvidence.start)}-${escapeHtml(bestEvidence.end)}</div>` : ""}
      <div class="evidence-box">${renderMarkdown(item.evidence_chunk || "未找到对应 source_id 的原文 chunk")}</div>
    </details>
    <div class="edit">
      <label>head<input id="h-${escapeAttr(item.review_id)}" value="${escapeAttr(item.head || "")}"></label>
      <label>relation<input id="r-${escapeAttr(item.review_id)}" value="${escapeAttr(relation)}"></label>
      <label>tail<input id="t-${escapeAttr(item.review_id)}" value="${escapeAttr(item.tail || "")}"></label>
    </div>
    <div class="edit">
      <label>三元组正确性<select id="tc-${escapeAttr(item.review_id)}"><option value="1">正确</option><option value="0">错误</option></select></label>
      <label>证据支持<select id="es-${escapeAttr(item.review_id)}"><option value="full">完整支持</option><option value="partial">部分支持</option><option value="none">不支持</option></select></label>
      <label>关系映射<select id="rmc-${escapeAttr(item.review_id)}"><option value="1">正确</option><option value="0">错误</option></select></label>
    </div>
    <textarea id="c-${escapeAttr(item.review_id)}" placeholder="审核备注"></textarea>
    <div class="actions">
      <button class="ok" onclick="decide(${jsArg(item.review_id)}, 'accept')">通过</button>
      <button class="primary" onclick="decide(${jsArg(item.review_id)}, 'modify')">修改后通过</button>
      <button class="bad" onclick="decide(${jsArg(item.review_id)}, 'reject')">拒绝</button>
      <button class="warn" onclick="decide(${jsArg(item.review_id)}, 'defer')">暂缓</button>
    </div>
  </article>`;
}

async function decide(id, decision) {
  const correctedTriple = decision === "modify" ? {
    head: document.getElementById(`h-${id}`).value,
    relation: document.getElementById(`r-${id}`).value,
    tail: document.getElementById(`t-${id}`).value
  } : null;
  const body = {
    file: state.file,
    review_id: id,
    decision,
    comment: document.getElementById(`c-${id}`).value,
    annotation: {
      triple_correct: Number(document.getElementById(`tc-${id}`).value),
      evidence_support: document.getElementById(`es-${id}`).value,
      relation_mapping_correct: Number(document.getElementById(`rmc-${id}`).value),
      corrected_triple: correctedTriple
    },
    modifications: correctedTriple
  };
  await api("/api/decision", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  await loadAll();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function confidenceDisplay(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value.toFixed(4);
  if (typeof value === "string") {
    const matched = value.match(/-?\d+(\.\d+)?/);
    if (matched) return Number(matched[0]).toFixed(4);
  }
  return "0.0000";
}

function renderMarkdown(value) {
  const text = String(value ?? "").replace(/\r\n/g, "\n");
  const lines = text.split("\n");
  const html = [];
  let paragraph = [];
  let listType = "";
  let inCode = false;
  let codeLines = [];
  let displayMathOpen = "";
  let displayMathClose = "";
  let displayMathLines = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  }

  function closeList() {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = "";
  }

  function flushDisplayMath() {
    if (!displayMathOpen) return;
    html.push(`<div class="math-display">${escapeHtml([displayMathOpen, ...displayMathLines, displayMathClose].join("\n"))}</div>`);
    displayMathOpen = "";
    displayMathClose = "";
    displayMathLines = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (/^```/.test(line.trim())) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        flushParagraph();
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }

    const mathLine = line.replace(/^>\s?/, "").trim();
    if (displayMathOpen) {
      if (mathLine === displayMathClose) {
        flushDisplayMath();
      } else {
        displayMathLines.push(line.replace(/^>\s?/, ""));
      }
      continue;
    }

    if (mathLine === "$$" || mathLine === "\\[") {
      flushParagraph();
      closeList();
      displayMathOpen = mathLine;
      displayMathClose = mathLine === "$$" ? "$$" : "\\]";
      displayMathLines = [];
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      closeList();
      html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        html.push(`<${nextType}>`);
        listType = nextType;
      }
      html.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      continue;
    }

    closeList();
    paragraph.push(line.trim());
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  flushDisplayMath();
  flushParagraph();
  closeList();
  return html.join("");
}

function inlineMarkdown(value) {
  const mathSegments = [];
  const protectedText = String(value ?? "").replace(/(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?!\s)(?:\\.|[^$])+\$)/g, match => {
    const token = `%%MATHPLACEHOLDER${mathSegments.length}%%`;
    mathSegments.push(match);
    return token;
  });

  const rendered = escapeHtml(protectedText)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/_([^_]+)_/g, "<em>$1</em>");

  return rendered.replace(/%%MATHPLACEHOLDER(\d+)%%/g, (_, index) => escapeHtml(mathSegments[Number(index)]));
}

function waitForMathJax(maxWaitMs = 6000) {
  if (window.MathJax && window.MathJax.typesetPromise) return Promise.resolve(true);
  if (window.mathJaxLoadFailed) return Promise.resolve(false);
  const ready = window.mathJaxReady || Promise.resolve();
  const timeout = new Promise(resolve => setTimeout(() => resolve(false), maxWaitMs));
  return Promise.race([ready.then(() => !!(window.MathJax && window.MathJax.typesetPromise)), timeout]);
}

function typesetMath(container) {
  mathTypesetQueue = mathTypesetQueue
    .catch(() => {})
    .then(() => waitForMathJax())
    .then(ready => {
      if (!ready || !container || !container.isConnected) return;
      if (window.MathJax.typesetClear) {
        window.MathJax.typesetClear([container]);
      }
      return window.MathJax.typesetPromise([container]);
    })
    .catch(err => console.warn("MathJax render failed", err));
}

function jsArg(value) {
  return `decodeURIComponent('${encodeURIComponent(String(value ?? ""))}')`;
}

init().catch(err => {
  document.getElementById("cards").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
});
</script>
</body>
</html>"""


class ReviewHandler(BaseHTTPRequestHandler):
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

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.send_html()
            elif parsed.path == "/api/files":
                self.send_json(list_review_files())
            elif parsed.path == "/api/summary":
                review_file = query.get("file", [""])[0]
                self.ensure_safe_file(review_file)
                self.send_json(build_summary(
                    review_file,
                    min_confidence=parse_float_arg(query, "min_confidence"),
                    max_confidence=parse_float_arg(query, "max_confidence"),
                    include_done=query.get("include_done", ["0"])[0] == "1",
                ))
            elif parsed.path == "/api/items":
                self.handle_items(query)
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/decision":
            self.send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.handle_decision(payload)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_items(self, query):
        review_file = query.get("file", [""])[0]
        self.ensure_safe_file(review_file)
        relation = query.get("relation", [""])[0]
        min_confidence = parse_float_arg(query, "min_confidence")
        max_confidence = parse_float_arg(query, "max_confidence")
        include_done = query.get("include_done", ["0"])[0] == "1"

        result = []
        for item in load_review_items(review_file):
            if not isinstance(item, dict):
                continue
            if not include_done and review_status(item) in {"accepted", "rejected"}:
                continue
            conf = confidence_value(item)
            if min_confidence is not None and conf < min_confidence:
                continue
            if max_confidence is not None and conf > max_confidence:
                continue
            if relation and item_relation(item) != relation:
                continue
            result.append(item_for_response(review_file, item))
        result.sort(key=lambda x: (item_relation(x), x.get("_confidence", 0.0)))
        self.send_json({"items": result})

    def handle_decision(self, payload):
        review_file = payload.get("file", "")
        self.ensure_safe_file(review_file)
        review_id = payload.get("review_id", "")
        decision = payload.get("decision", "")
        if decision not in {"accept", "reject", "modify", "defer"}:
            raise ValueError("decision 必须是 accept/reject/modify/defer")

        path = REJECTED_DIR / review_file
        data = load_review_items(review_file)
        for item in data:
            if isinstance(item, dict) and item.get("review_id") == review_id:
                target = item
                break
        else:
            raise ValueError("未找到对应待审三元组")

        original = copy.deepcopy(target)
        annotation = payload.get("annotation") or {}
        if not isinstance(annotation, dict):
            annotation = {}
        annotation.setdefault("triple_correct", 1 if decision == "accept" else 0)
        annotation.setdefault("evidence_support", "full" if decision in {"accept", "modify"} else "none")
        annotation.setdefault("relation_mapping_correct", 1 if decision == "accept" else 0)
        if decision == "modify":
            annotation["triple_correct"] = 0
            annotation["relation_mapping_correct"] = 0
            annotation["corrected_triple"] = payload.get("modifications") or annotation.get("corrected_triple")
        else:
            annotation.setdefault("corrected_triple", None)

        if decision == "modify":
            modifications = payload.get("modifications") or {}
            for key in ["head", "relation", "tail"]:
                if key in modifications:
                    target[key] = str(modifications[key]).strip()
            target.pop("corrected_relation", None)

        target["review_annotation"] = annotation

        accepted_written = False
        if decision in {"accept", "modify"}:
            target["decision"] = "human_accept"
            accepted_written = append_unique_triplet(review_file, target)
            target["review_status"] = "accepted"
        elif decision == "reject":
            target["decision"] = "human_reject"
            target["review_status"] = "rejected"
        else:
            target["review_status"] = "deferred"

        target["review_decision"] = decision
        target["review_comment"] = payload.get("comment")
        target["review_time"] = now_iso()
        write_json(path, data)

        append_review_log(review_file, {
            "review_id": review_id,
            "decision": decision,
            "accepted_written": accepted_written,
            "comment": payload.get("comment"),
            "annotation": annotation,
            "time": target["review_time"],
            "original": original,
            "current": copy.deepcopy(target),
        })
        self.send_json({"ok": True, "accepted_written": accepted_written})

    def ensure_safe_file(self, review_file):
        if not review_file or Path(review_file).name != review_file or review_file not in list_review_files():
            raise ValueError("非法或不存在的待审文件")

    def log_message(self, fmt, *args):
        return


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("TRIPLET_REVIEW_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    print(f"三元组审核界面已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()


if __name__ == "__main__":
    main()
