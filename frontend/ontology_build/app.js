let state = { result: null, markdownFiles: [] };
const SIDEBAR_WIDTH_KEY = "ontologyBuild.sidebarWidth";

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function init() {
  initSidebarResize();
  const info = await api("/api/init");
  state.markdownFiles = info.markdown_files;
  document.getElementById("markdownSelect").innerHTML = state.markdownFiles
    .map(path => `<option value="${escapeAttr(path)}">${escapeHtml(path)}</option>`).join("");
  await refresh();
}

function initSidebarResize() {
  const savedWidth = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
  if (Number.isFinite(savedWidth)) {
    setSidebarWidth(savedWidth);
  }
  const resizer = document.getElementById("sidebarResizer");
  if (!resizer) return;
  let resizing = false;
  resizer.addEventListener("pointerdown", event => {
    resizing = true;
    resizer.setPointerCapture(event.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });
  resizer.addEventListener("pointermove", event => {
    if (!resizing) return;
    setSidebarWidth(event.clientX);
  });
  const stopResize = event => {
    if (!resizing) return;
    resizing = false;
    try {
      resizer.releasePointerCapture(event.pointerId);
    } catch (_) {}
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const width = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"), 10);
    if (Number.isFinite(width)) {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width));
    }
  };
  resizer.addEventListener("pointerup", stopResize);
  resizer.addEventListener("pointercancel", stopResize);
}

function setSidebarWidth(width) {
  const maxWidth = Math.max(360, window.innerWidth - 420);
  const nextWidth = Math.min(Math.max(Number(width) || 360, 280), maxWidth);
  document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
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
    reviewer: document.getElementById("reviewer").value,
    base_policy: document.getElementById("basePolicy").value
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
