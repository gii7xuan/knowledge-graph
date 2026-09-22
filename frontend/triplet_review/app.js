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
