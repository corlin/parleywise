/* 合规初审工作台前端：原生 JS，无构建步骤。所有用户/模型文本一律用 textContent 写入，避免 XSS。 */
"use strict";

// ---------- 工具 ----------
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // 仅用于写死的静态片段
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw Object.assign(new Error("请先登录"), { auth: true }); }
  if (!r.ok) throw new Error(data.detail || `请求失败（${r.status}）`);
  return data;
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 4000 : 2000);
}
const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const fmtDur = ms => { const s = Math.round((ms || 0) / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const STAGES = [["keyword", "关键词初筛"], ["triage", "模型初筛（召回优先）"], ["review", "模型复核（压误报）"], ["verify", "校验原文片段与条款"]];
const SEV = { high: "高", medium: "中", low: "低" };
let META = { reject_reasons: {}, conclusions: {}, rules: [], user: null };
let cleanup = []; // 路由切换时执行的清理函数

// ---------- 路由 ----------
async function route() {
  cleanup.forEach(f => f()); cleanup = [];
  const hash = location.hash || "#/";
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active",
    (a.dataset.nav === "metrics") === hash.startsWith("#/metrics")));
  const app = $("#app"); app.replaceChildren();
  try {
    let m;
    if ((m = hash.match(/^#\/doc\/(\d+)/))) await pageDoc(app, +m[1]);
    else if (hash.startsWith("#/new")) await pageNew(app);
    else if (hash.startsWith("#/metrics")) await pageMetrics(app);
    else await pageList(app);
  } catch (e) { if (!e.auth) app.append(h("div", { class: "empty" }, h("h3", {}, "出错了"), e.message)); }
  window.scrollTo(0, 0);
}

// ---------- 材料列表 ----------
async function pageList(app) {
  const docs = await api("/api/documents");
  app.append(h("div", { class: "page-head" }, h("h1", {}, "材料"),
    h("span", { class: "sub" }, `${docs.length} 份`), h("div", { class: "spacer" }),
    h("a", { class: "btn primary", href: "#/new" }, "＋ 新建审查")));
  if (!docs.length) {
    app.append(h("div", { class: "panel empty" }, h("h3", {}, "还没有材料"),
      h("p", {}, "粘贴一段宣传推介文案或上传文件，AI 会先做初审，再由你逐条复核。"),
      h("a", { class: "btn primary", href: "#/new" }, "新建审查"), " ",
      h("a", { class: "btn", href: "#/new?golden=1" }, "用黄金集样例试一试")));
    return;
  }
  const tb = h("tbody");
  for (const d of docs) {
    const ai = d.status === "done";
    const pct = d.ai_findings ? Math.round(100 * d.ai_decided / d.ai_findings) : 100;
    let st;
    if (d.status === "failed") st = h("span", {}, h("span", { class: "dot fail" }), " 初审失败");
    else if (!ai) st = h("span", {}, h("span", { class: "dot run" }), " ", (STAGES.find(s => s[0] === d.stage) || [0, "排队中"])[1]);
    else if (d.review_status === "completed") st = h("span", {}, h("span", { class: "dot ok" }), " 已完成");
    else if (d.arm === "control" || !d.ai_findings) st = h("span", { class: "muted" }, d.review_status === "in_progress" ? "复核中" : "待复核");
    else st = h("span", {}, h("span", { class: "progress-bar" }, h("i", { style: `width:${pct}%` })), ` ${d.ai_decided}/${d.ai_findings}`);
    tb.append(h("tr", { class: "click", onclick: () => (location.hash = `#/doc/${d.id}`) },
      h("td", { class: "title", title: d.title }, d.title),
      h("td", { class: "hide-sm" }, d.doc_type === "public" ? "公募" : "私募"),
      h("td", {}, d.arm === "control" ? h("span", { class: "chip warn" }, "对照组") : h("span", { class: "chip accent" }, "AI 辅助"),
        d.mode === "offline" ? h("span", { class: "chip", style: "margin-left:4px" }, "仅关键词") : null),
      h("td", {}, ai ? (d.arm === "control" && d.review_status !== "completed" ? h("span", { class: "faint" }, "已隐藏")
        : `${d.snippets} 处 / ${d.ai_findings} 条`) : "—",
        d.manual_findings ? h("span", { class: "chip manual", style: "margin-left:6px" }, `+${d.manual_findings} 人工`) : null),
      h("td", {}, st),
      h("td", { class: "hide-sm" }, d.active_ms ? fmtDur(d.active_ms) : "—"),
      h("td", {}, d.conclusion ? META.conclusions[d.conclusion] : "—"),
      h("td", { class: "hide-sm muted" }, fmtTime(d.created_at))));
  }
  app.append(h("div", { class: "panel" }, h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ...["材料", "类型", "组别", "AI 意见", "状态", "审查用时", "结论", "创建"].map((t, i) =>
      h("th", { class: [1, 5, 7].includes(i) ? "hide-sm" : null }, t)))), tb)));
  if (docs.some(d => d.status === "queued" || d.status === "running")) {
    const t = setTimeout(route, 3000);
    cleanup.push(() => clearTimeout(t));
  }
}

// ---------- 新建审查 ----------
async function pageNew(app) {
  let file = null, goldenId = "", tab = "paste";
  const golden = await api("/api/golden").catch(() => []);
  app.append(h("div", { class: "page-head" }, h("a", { class: "btn ghost", href: "#/" }, "← 返回"), h("h1", {}, "新建审查")));
  const title = h("input", { type: "text", placeholder: "可留空，默认取文件名或正文开头", maxlength: 80 });
  const docType = h("select", {}, h("option", { value: "public" }, "公募基金宣传推介材料"), h("option", { value: "private" }, "私募基金宣传推介材料"));
  const text = h("textarea", { class: "input", rows: 14, placeholder: "把宣传推介材料的全文粘贴到这里（含文末风险提示和声明）……" });
  const count = h("span", { class: "faint small" }, "0 字");
  text.addEventListener("input", () => { count.textContent = `${text.value.length} 字`; goldenId = ""; });
  const fileInput = h("input", { type: "file", accept: ".txt,.md,.docx,.pdf", hidden: true });
  const dropLabel = h("div", {}, h("b", {}, "拖入文件，或点击选择"), h("div", { class: "small", style: "margin-top:6px" }, "支持 .docx / .pdf / .txt / .md；扫描件和图片暂不支持"));
  const drop = h("div", { class: "drop", onclick: () => fileInput.click() }, dropLabel);
  const setFile = f => { file = f; dropLabel.replaceChildren(h("b", {}, f.name), h("div", { class: "small" }, `${(f.size / 1024).toFixed(1)} KB · 点击更换`)); };
  fileInput.addEventListener("change", () => fileInput.files[0] && setFile(fileInput.files[0]));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]); });
  const pastePane = h("div", {}, text, h("div", { class: "row", style: "justify-content:space-between;margin-top:6px" }, count));
  const filePane = h("div", { hidden: true }, drop, fileInput);
  const tabs = h("div", { class: "tabs" });
  const setTab = t => { tab = t; pastePane.hidden = t !== "paste"; filePane.hidden = t !== "file";
    tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); };
  tabs.append(h("button", { "data-t": "paste", class: "on", onclick: () => setTab("paste") }, "粘贴文本"),
    h("button", { "data-t": "file", onclick: () => setTab("file") }, "上传文件"));
  const control = h("input", { type: "checkbox" });
  const offline = h("input", { type: "checkbox" });
  const submit = h("button", { class: "btn primary", style: "width:100%;justify-content:center" }, "开始 AI 初审");
  submit.addEventListener("click", async () => {
    const fd = new FormData();
    fd.append("title", title.value); fd.append("doc_type", docType.value);
    fd.append("arm", control.checked ? "control" : "ai"); fd.append("mode", offline.checked ? "offline" : "full");
    fd.append("golden_case_id", goldenId);
    if (tab === "file") { if (!file) return toast("请先选择文件", true); fd.append("file", file); }
    else { if (!text.value.trim()) return toast("请粘贴材料内容", true); fd.append("text", text.value); }
    submit.disabled = true; submit.textContent = "提交中…";
    try { const r = await api("/api/documents", { method: "POST", body: fd }); location.hash = `#/doc/${r.id}`; }
    catch (e) { toast(e.message, true); submit.disabled = false; submit.textContent = "开始 AI 初审"; }
  });
  const goldenSel = h("select", { class: "input" }, h("option", { value: "" }, "选择一个合成样例…"),
    ...golden.map(g => h("option", { value: g.id }, `${g.id} · ${g.text.slice(0, 26)}…`)));
  goldenSel.addEventListener("change", () => {
    const g = golden.find(x => x.id === goldenSel.value); if (!g) return;
    setTab("paste"); text.value = g.text; count.textContent = `${text.value.length} 字`;
    title.value = `黄金集 ${g.id}`; docType.value = g.doc_type; goldenId = g.id;
  });
  app.append(h("div", { class: "new-wrap" },
    h("div", { class: "panel", style: "padding:20px" }, tabs, pastePane, filePane),
    h("div", {},
      h("div", { class: "panel", style: "padding:20px" },
        h("label", { class: "field" }, h("span", {}, "材料名称"), title),
        h("label", { class: "field" }, h("span", {}, "材料类型"), docType),
        h("label", { class: "check" }, control, h("span", {}, "对照组（不显示 AI 意见）", h("small", {}, "AI 仍在后台初审，但审核人看不到，用于测量“没有 AI 时”的耗时和发现数。完成后可对比。"))),
        h("label", { class: "check" }, offline, h("span", {}, "仅关键词（不调用模型）", h("small", {}, "几秒出结果，只用于演示界面。"))),
        META.key_configured ? null : h("p", { class: "small", style: "color:var(--high);margin:0 0 12px" }, "服务器未配置模型 key，只能用“仅关键词”模式。"),
        submit,
        h("p", { class: "faint small", style: "margin:12px 0 0" }, "材料会经 LiteLLM 网关发送给模型做初审。正式试点前请确认数据出境与保密要求。")),
      golden.length ? h("div", { class: "panel", style: "padding:16px 20px;margin-top:12px" },
        h("div", { class: "field", style: "margin:0" }, h("span", {}, "黄金集样例（演示）"), goldenSel)) : null)));
  if (location.hash.includes("golden=1")) goldenSel.focus();
}

// ---------- 复核页 ----------
async function pageDoc(app, id) {
  let doc = await api(`/api/documents/${id}`);
  if (doc.status !== "done") return pageProcessing(app, doc);
  const S = { doc, active: 0, editing: null, rejecting: null, expanded: new Set(), focusAt: performance.now() };
  const ro = () => S.doc.review_status === "completed";

  // —— 计时：页面可见且 2 分钟内有操作才计时，每 15 秒上报一次
  let pendingMs = 0, lastAct = Date.now(), shownMs = doc.active_ms;
  const act = () => (lastAct = Date.now());
  ["mousemove", "keydown", "scroll", "click", "wheel"].forEach(ev => window.addEventListener(ev, act, { passive: true }));
  const isActive = () => !ro() && document.visibilityState === "visible" && Date.now() - lastAct < 120000;
  const flush = (beacon = false) => {
    if (!pendingMs) return;
    const body = JSON.stringify({ active_ms: Math.min(pendingMs, 30000) });
    pendingMs = 0;
    if (beacon) navigator.sendBeacon(`/api/documents/${id}/heartbeat`, new Blob([body], { type: "application/json" }));
    else fetch(`/api/documents/${id}/heartbeat`, { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch(() => {});
  };
  const tick = setInterval(() => {
    const on = isActive();
    if (on) { pendingMs += 1000; shownMs += 1000; }
    const t = $("#timer"); if (t) { t.lastChild.textContent = fmtDur(shownMs); t.classList.toggle("idle", !on); }
  }, 1000);
  const beat = setInterval(() => flush(), 15000);
  const onHide = () => flush(true);
  window.addEventListener("pagehide", onHide);
  document.addEventListener("visibilitychange", onHide);
  cleanup.push(() => { clearInterval(tick); clearInterval(beat); flush(true);
    ["mousemove", "keydown", "scroll", "click", "wheel"].forEach(ev => window.removeEventListener(ev, act));
    window.removeEventListener("pagehide", onHide); document.removeEventListener("visibilitychange", onHide); });

  // —— 数据整理：按片段分组，编号按原文位置
  function groups() {
    const m = new Map();
    for (const f of S.doc.findings) { if (!m.has(f.group_idx)) m.set(f.group_idx, []); m.get(f.group_idx).push(f); }
    const arr = [...m.values()].map(fs => ({ fs, start: Math.min(...fs.flatMap(f => f.spans.map(s => s[0])), 1e9) }));
    arr.sort((a, b) => a.start - b.start || (a.fs[0].source === "manual") - (b.fs[0].source === "manual"));
    arr.forEach((g, i) => (g.no = i + 1));
    return arr;
  }
  const flat = () => groups().flatMap(g => g.fs);
  const sevOf = f => f.source === "manual" ? "manual" : (f.rule.severity || "low");

  function render() {
    const d = S.doc, G = groups(), F = G.flatMap(g => g.fs);
    if (S.active >= F.length) S.active = Math.max(0, F.length - 1);
    const aiF = d.findings.filter(f => f.source === "ai");
    const decided = aiF.filter(f => f.decision).length;
    app.replaceChildren();
    // 顶栏
    app.append(h("div", { class: "rv-bar" },
      h("a", { class: "btn ghost", href: "#/" }, "←"),
      h("h1", { title: d.title }, d.title),
      d.arm === "control" ? h("span", { class: "chip warn" }, "对照组") : h("span", { class: "chip accent" }, "AI 辅助"),
      h("span", { class: "chip" }, d.doc_type === "public" ? "公募" : "私募"),
      h("div", { class: "spacer" }),
      d.arm === "ai" && !ro() ? h("span", { class: "muted" }, `已处理 ${decided}/${aiF.length}`) : null,
      ro() ? null : h("span", { class: "timer", id: "timer", title: "有效审查时长：页面可见且 2 分钟内有操作才计时" }, "⏱ ", h("span", {}, fmtDur(shownMs))),
      h("a", { class: "btn", href: `/api/documents/${id}/report.html`, target: "_blank" }, ro() ? "查看报告" : "预览报告"),
      META.user.role === "admin" ? h("button", { class: "btn ghost", onclick: delDoc, title: "删除这份材料及其复核记录" }, "删除") : null,
      ro() ? h("button", { class: "btn", onclick: reopen, disabled: d.arm === "control" }, "重新打开")
        : h("button", { class: "btn primary", onclick: openComplete }, "完成审查")));
    if (ro()) app.append(h("div", { class: "banner done" },
      h("b", {}, `已完成 · ${META.conclusions[d.conclusion] || ""}`),
      h("span", {}, `审核人 ${d.reviewer || "—"} · 有效用时 ${fmtDur(d.active_ms)} · ${fmtTime(d.review_completed_at)}`),
      h("div", { class: "spacer" }),
      h("a", { class: "btn sm", href: `/api/documents/${id}/report.md` }, "导出 Markdown"),
      h("a", { class: "btn sm", href: `/api/documents/${id}/report.json` }, "导出 JSON")));

    // 左：原文
    const textEl = h("div", { class: "doc-text", id: "doctext" });
    renderText(textEl, G);
    const left = h("section", { class: "panel rv-text" },
      h("div", { class: "head" }, "原文", h("span", { class: "faint small" }, `${d.text.length} 字`), h("div", { class: "spacer" }),
        ro() ? null : h("span", { class: "faint small" }, "划选文字可添加 AI 漏报的问题")), textEl);
    // 右：意见
    const side = h("section", { class: "panel rv-side" });
    const head = h("div", { class: "head" }, d.ai_hidden ? "我标注的问题" : "初审意见",
      h("span", { class: "faint small" }, d.ai_hidden ? `${F.length} 项` : `${G.length} 处片段 · ${F.length} 条意见`));
    side.append(head);
    if (d.ai_hidden) side.append(h("div", { class: "hint" }, "对照组：AI 意见已隐藏。请通读原文，在原文中划选有问题的文字并添加。完成审查后会显示 AI 意见供对比。"));
    else if (d.arm === "control") side.append(h("div", { class: "hint" }, "对照组已完成：下面同时列出你标注的问题（紫色）和当时隐藏的 AI 意见，便于对比。"));
    const list = h("div", { class: "groups" });
    if (!G.length) list.append(h("div", { class: "empty" }, h("h3", {}, d.ai_hidden ? "还没有标注问题" : "AI 未发现疑似问题"),
      d.ai_hidden ? "如果通读后确认没有问题，可以直接完成审查。" : "请通读原文确认；如发现问题，划选原文添加。"));
    let idx = 0;
    for (const g of G) {
      const quotes = [...new Set(g.fs.flatMap(f => [f.quote, ...f.also_quotes]))];
      const gEl = h("div", { class: "group", "data-group": g.no },
        h("div", { class: "group-head", onclick: () => focusIdx(F.indexOf(g.fs[0]), true) },
          h("span", { class: "num" }, g.no), h("span", { class: "q" }, quotes.map(q => `「${q}」`).join(" "))));
      for (const f of g.fs) gEl.append(findingEl(f, idx++, F));
      list.append(gEl);
    }
    side.append(list);
    if (!ro()) side.append(h("div", { class: "keys" }, h("span", {}, h("kbd", {}, "J"), "/", h("kbd", {}, "K"), " 切换"),
      d.ai_hidden ? null : [h("span", {}, h("kbd", {}, "A"), " 采纳"), h("span", {}, h("kbd", {}, "R"), " 驳回"), h("span", {}, h("kbd", {}, "E"), " 修改")],
      h("span", {}, h("kbd", {}, "Esc"), " 取消")));
    if (ro() && d.golden) side.append(goldenEl(d));
    app.append(h("div", { class: "rv" }, left, side));
    markFocus(false);
  }

  function renderText(el, G) {
    const text = S.doc.text, cuts = new Set([0, text.length]), items = [];
    for (const g of G) for (const f of g.fs) for (const [s, e] of f.spans) { cuts.add(s); cuts.add(e); items.push({ s, e, f, g }); }
    const pts = [...cuts].sort((a, b) => a - b);
    const badged = new Set();
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1], seg = text.slice(a, b);
      const cover = items.filter(x => x.s <= a && x.e >= b);
      if (!cover.length) { el.append(seg); continue; }
      const fs = cover.map(x => x.f);
      const live = fs.filter(f => f.decision !== "rejected");
      const top = (live.length ? live : fs).slice().sort((x, y) => sevRank(sevOf(x)) - sevRank(sevOf(y)))[0];
      const allDone = fs.every(f => f.decision);
      const cls = ["hl", `sev-${sevOf(top)}`, !live.length ? "rejected" : allDone ? "done" : ""].join(" ");
      const span = h("span", { class: cls, "data-fids": fs.map(f => f.id).join(","), "data-start": a });
      const gs = [...new Set(cover.map(x => x.g))].sort((x, y) => x.no - y.no)
        .filter(g => !badged.has(g.no) && a === Math.min(...g.fs.flatMap(f => f.spans.map(s => s[0]))));
      if (gs.length) { span.dataset.badge = gs.map(g => g.no).join(","); gs.forEach(g => badged.add(g.no)); }
      span.textContent = seg;
      span.addEventListener("click", () => { if (window.getSelection().toString()) return;
        const F = flat(); const i = F.findIndex(f => f.id === top.id); focusIdx(i, true); });
      el.append(span);
    }
    if (!ro()) el.addEventListener("mouseup", onSelect);
  }
  const sevRank = s => ({ high: 0, manual: 1, medium: 2, low: 3 }[s] ?? 4);

  function findingEl(f, i, F) {
    const r = f.rule, d = S.doc, editable = !ro();
    const isAct = i === S.active;
    const state = f.decision === "accepted" ? h("span", { class: "chip ok" }, f.source === "manual" ? "人工补充" : "已采纳")
      : f.decision === "modified" ? h("span", { class: "chip mod" }, "已修改")
      : f.decision === "rejected" ? h("span", { class: "chip rej" }, `已驳回 · ${META.reject_reasons[f.reject_reason] || ""}`)
      : h("span", { class: "faint small" }, d.arm === "control" ? "对照组时隐藏的 AI 意见" : "待处理");
    const el = h("div", { class: "f" + (isAct ? " active" : ""), "data-fid": f.id, onclick: e => { if (e.target.closest("button,textarea,input")) return; focusIdx(i, true); } },
      h("div", { class: "f-title" },
        h("b", {}, `${r.id} ${r.title || ""}`),
        f.source === "manual" ? h("span", { class: "chip manual" }, "人工") : h("span", { class: `chip ${r.severity}` }, `${SEV[r.severity] || ""}风险`),
        f.source === "ai" && f.stage === "keyword" ? h("span", { class: "chip" }, "关键词") : null),
      h("div", { class: "f-meta" }, `${r.article || ""} · ${(((META.sources[r.source] || "").match(/《[^》]+》/) || [r.source || ""])[0])}`,
        " ", h("button", { class: "link-btn", onclick: () => { S.expanded.has(f.id) ? S.expanded.delete(f.id) : S.expanded.add(f.id); render(); } },
          S.expanded.has(f.id) ? "收起条款" : "查看条款")),
      S.expanded.has(f.id) ? h("div", { class: "rule-desc" }, r.description, h("div", { class: "faint small", style: "margin-top:4px" }, "规则说明为条款转述，以监管原文为准。")) : null,
      h("div", { class: "f-body" },
        f.quote && f.source === "ai" ? h("div", {}, h("span", { class: "lbl" }, "片段"), `「${f.quote}」`) : null,
        f.reason ? h("div", {}, h("span", { class: "lbl" }, f.source === "manual" ? "说明" : "理由"), f.reason) : null,
        h("div", { style: f.decision === "modified" ? "text-decoration:line-through;color:var(--faint)" : "" },
          h("span", { class: "lbl" }, "建议"), f.suggestion || "按规则修改或删除"),
        f.decision === "modified" ? h("div", { style: "color:var(--mod)" }, h("span", { class: "lbl" }, "修改为"), f.edited_suggestion) : null,
        f.note && f.decision !== "modified" ? h("div", {}, h("span", { class: "lbl" }, "备注"), f.note) : null));
    if (editable && f.source === "ai") {
      el.append(h("div", { class: "f-actions" },
        h("button", { class: "btn sm accept" + (f.decision === "accepted" ? " on" : ""), onclick: () => decide(f, "accepted") }, "✓ 采纳"),
        h("button", { class: "btn sm modify" + (f.decision === "modified" ? " on" : ""), onclick: () => startEdit(f) }, "✎ 修改"),
        h("button", { class: "btn sm reject" + (f.decision === "rejected" ? " on" : ""), onclick: () => startReject(f) }, "✕ 驳回"),
        f.decision ? h("button", { class: "btn sm ghost", onclick: () => decide(f, null) }, "撤销") : null,
        h("span", { class: "f-state" }, state)));
    } else if (editable && f.source === "manual") {
      el.append(h("div", { class: "f-actions" }, h("button", { class: "btn sm ghost", onclick: () => delManual(f) }, "删除"), h("span", { class: "f-state" }, state)));
    } else el.append(h("div", { class: "f-actions" }, h("span", { class: "f-state" }, state)));
    if (S.rejecting === f.id) {
      const note = h("input", { class: "input", placeholder: "补充说明（选“其他”时必填）", value: f.note || "" });
      const keys = Object.entries(META.reject_reasons);
      el.append(h("div", { class: "reasons" }, ...keys.map(([k, v], j) =>
        h("button", { class: f.reject_reason === k ? "on" : "", onclick: () => decide(f, "rejected", { reject_reason: k, note: note.value }) }, `${j + 1} ${v}`))),
        h("div", { class: "editor" }, note));
    }
    if (S.editing === f.id) {
      const ta = h("textarea", { class: "input", rows: 3 }); ta.value = f.edited_suggestion || f.suggestion || "";
      const save = () => decide(f, "modified", { edited_suggestion: ta.value });
      ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); });
      el.append(h("div", { class: "editor" }, h("span", { class: "small muted" }, "修改后的意见（会写进报告）"), ta,
        h("div", { class: "row" }, h("button", { class: "btn sm primary", onclick: save }, "保存"),
          h("button", { class: "btn sm", onclick: () => { S.editing = null; render(); } }, "取消"), h("span", { class: "faint small" }, "⌘/Ctrl + Enter 保存"))));
      setTimeout(() => ta.focus(), 0);
    }
    return el;
  }

  function goldenEl(d) {
    const exp = d.golden.expected.map(e => e.rule_id), acc = d.golden.acceptable || [];
    const final = new Set(d.findings.filter(f => ["accepted", "modified"].includes(f.decision)).map(f => f.rule_id));
    return h("div", { class: "hint", style: "margin:0 12px 12px;background:var(--bg);color:var(--text)" },
      h("b", {}, `黄金集 ${d.golden.id} 预期标注（演示）`), h("br"),
      exp.length ? exp.map(r => h("span", { class: "chip " + (final.has(r) ? "ok" : "high"), style: "margin:4px 4px 0 0" }, `${r} ${final.has(r) ? "已认定" : "未认定"}`)) : "预期无问题",
      acc.length ? h("div", { class: "small muted", style: "margin-top:4px" }, `可接受的次要命中：${acc.join("、")}`) : null);
  }

  // —— 焦点与快捷键
  function focusIdx(i, scrollText) {
    const F = flat(); if (i < 0 || i >= F.length) return;
    if (i !== S.active) { S.focusAt = performance.now(); S.rejecting = null; S.editing = null; }
    S.active = i; render(); markFocus(scrollText);
  }
  function markFocus(scrollText) {
    const F = flat(), f = F[S.active]; if (!f) return;
    const card = app.querySelector(`[data-fid="${f.id}"]`);
    if (card) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    app.querySelectorAll(".hl").forEach(s => {
      const on = s.dataset.fids.split(",").includes(String(f.id));
      s.classList.toggle("focus", on);
    });
    const hl = [...app.querySelectorAll(".hl.focus")][0];
    if (hl && scrollText) { hl.scrollIntoView({ block: "center", behavior: "smooth" }); hl.classList.add("flash"); }
  }
  function onKey(e) {
    if ($(".modal-bg") || e.target.matches("input,textarea,select")) {
      if (e.key === "Escape") { S.editing = S.rejecting = null; closeModal(); render(); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const F = flat(), f = F[S.active];
    const k = e.key.toLowerCase();
    if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); focusIdx(Math.min(S.active + 1, F.length - 1), true); }
    else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); focusIdx(Math.max(S.active - 1, 0), true); }
    else if (e.key === "Escape") { S.editing = S.rejecting = null; render(); }
    else if (!f || ro() || f.source !== "ai") return;
    else if (k === "a") decide(f, "accepted");
    else if (k === "e") { e.preventDefault(); startEdit(f); }
    else if (k === "r") startReject(f);
    else if (S.rejecting === f.id && /^[1-5]$/.test(e.key)) {
      const key = Object.keys(META.reject_reasons)[+e.key - 1];
      if (key === "other") { const inp = app.querySelector(`[data-fid="${f.id}"] .editor input`); inp && inp.focus(); toast("请填写说明后再点“其他”"); }
      else decide(f, "rejected", { reject_reason: key });
    }
  }
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));

  // —— 操作
  function takeElapsed() {
    const ms = Math.min(Math.round(performance.now() - S.focusAt), 600000);
    S.focusAt = performance.now();
    return ms;
  }
  async function decide(f, decision, extra = {}) {
    if (ro()) return;
    try {
      const nf = await api(`/api/findings/${f.id}`, { method: "PATCH", json: { decision, ...extra, elapsed_ms: takeElapsed() } });
      Object.assign(f, nf); S.editing = S.rejecting = null;
      // 处理完自动跳到下一条未处理的
      const F = flat(); const next = F.findIndex((x, j) => j > S.active && x.source === "ai" && !x.decision);
      if (decision && next >= 0) { S.active = next; S.focusAt = performance.now(); }
      render(); markFocus(true);
    } catch (e) { toast(e.message, true); }
  }
  function startEdit(f) { if (ro()) return; S.rejecting = null; S.editing = f.id; S.active = flat().findIndex(x => x.id === f.id); render(); }
  function startReject(f) { if (ro()) return; S.editing = null; S.rejecting = S.rejecting === f.id ? null : f.id; S.active = flat().findIndex(x => x.id === f.id); render(); }
  async function delManual(f) {
    if (!confirm("删除这条人工补充的问题？")) return;
    try { await api(`/api/findings/${f.id}`, { method: "DELETE" }); S.doc.findings = S.doc.findings.filter(x => x.id !== f.id); render(); }
    catch (e) { toast(e.message, true); }
  }
  async function delDoc() {
    if (!confirm(`删除「${S.doc.title}」及其全部复核记录？此操作不能撤销。`)) return;
    try { await api(`/api/documents/${id}`, { method: "DELETE" }); location.hash = "#/"; } catch (e) { toast(e.message, true); }
  }
  async function reopen() {
    try { await api(`/api/documents/${id}/reopen`, { json: {} }); S.doc = await api(`/api/documents/${id}`); render(); }
    catch (e) { toast(e.message, true); }
  }

  // —— 划选添加问题
  let pop = null;
  const hidePop = () => { if (pop) { pop.remove(); pop = null; } };
  cleanup.push(hidePop);
  function selOffsets(root) {
    const sel = window.getSelection(); if (!sel.rangeCount || sel.isCollapsed) return null;
    const rg = sel.getRangeAt(0);
    if (!root.contains(rg.startContainer) || !root.contains(rg.endContainer)) return null;
    const pre = document.createRange(); pre.setStart(root, 0); pre.setEnd(rg.startContainer, rg.startOffset);
    const s = pre.toString().length, e = s + rg.toString().length;
    // 去掉首尾空白
    const t = S.doc.text; let a = s, b = e;
    while (a < b && /\s/.test(t[a])) a++; while (b > a && /\s/.test(t[b - 1])) b--;
    return a < b ? [a, b] : null;
  }
  function onSelect(ev) {
    setTimeout(() => {
      hidePop();
      const root = $("#doctext"); const off = root && selOffsets(root); if (!off) return;
      const rect = window.getSelection().getRangeAt(0).getBoundingClientRect();
      pop = h("div", { class: "sel-pop", style: `left:${rect.left + window.scrollX}px;top:${rect.bottom + window.scrollY + 6}px` },
        h("button", { class: "btn sm primary", onmousedown: e => e.preventDefault(), onclick: () => { hidePop(); openManual(off); } },
          S.doc.ai_hidden ? "＋ 标注问题" : "＋ 添加 AI 漏报的问题"));
      document.body.append(pop);
    }, 0);
  }
  const onDown = e => { if (pop && !pop.contains(e.target)) hidePop(); };
  document.addEventListener("mousedown", onDown);
  cleanup.push(() => document.removeEventListener("mousedown", onDown));

  function openManual([s, e]) {
    const quote = S.doc.text.slice(s, e), t0 = performance.now();
    let rule = null;
    const rules = META.rules.filter(r => (r.applies_to || S.doc.doc_type) === S.doc.doc_type);
    const q = h("input", { class: "input", placeholder: "搜索规则编号或关键词，如 R02、保本、基准" });
    const listEl = h("div", { class: "rule-list" });
    const draw = () => {
      const kw = q.value.trim().toLowerCase();
      listEl.replaceChildren(...rules.filter(r => !kw || `${r.id} ${r.title} ${r.description}`.toLowerCase().includes(kw)).map(r =>
        h("div", { class: rule === r.id ? "on" : "", onclick: () => { rule = r.id; draw(); } }, h("b", {}, r.id), h("span", {}, r.title))));
    };
    q.addEventListener("input", draw); draw();
    const reason = h("textarea", { class: "input", rows: 2, placeholder: "为什么有问题（可选）" });
    const sugg = h("textarea", { class: "input", rows: 2, placeholder: "修改意见（可选，会写进报告）" });
    openModal(S.doc.ai_hidden ? "标注问题" : "添加 AI 漏报的问题",
      [h("div", { class: "quote-box" }, `「${quote}」`),
        h("div", { class: "field" }, h("span", {}, "违反的规则"), q, listEl),
        h("label", { class: "field" }, h("span", {}, "说明"), reason),
        h("label", { class: "field", style: "margin:0" }, h("span", {}, "修改意见"), sugg)],
      "添加", async () => {
        if (!rule) { toast("请选择规则", true); return false; }
        const nf = await api(`/api/documents/${id}/findings`, { json: { rule_id: rule, start: s, end: e, reason: reason.value,
          suggestion: sugg.value, elapsed_ms: Math.round(performance.now() - t0) } });
        S.doc.findings.push(nf); S.doc.manual_findings++; window.getSelection().removeAllRanges();
        render(); focusIdx(flat().findIndex(f => f.id === nf.id), false); toast("已添加");
      });
    setTimeout(() => q.focus(), 0);
  }

  function openComplete() {
    const d = S.doc, aiLeft = d.findings.filter(f => f.source === "ai" && !f.decision).length;
    if (d.arm === "ai" && aiLeft) { toast(`还有 ${aiLeft} 条 AI 意见未处理`, true); focusIdx(flat().findIndex(f => f.source === "ai" && !f.decision), true); return; }
    const final = d.findings.filter(f => ["accepted", "modified"].includes(f.decision)).length;
    const def = final ? "revise" : "pass";
    const choice = h("div", { class: "choice" }, ...Object.entries(META.conclusions).map(([k, v]) =>
      h("label", {}, h("input", { type: "radio", name: "concl", value: k, checked: k === def }), v)));
    const note = h("textarea", { class: "input", rows: 3, placeholder: "总体意见（可选，会写进报告）" });
    openModal("完成审查", [h("p", { class: "muted", style: "margin-top:0" }, `认定问题 ${final} 项（含人工补充 ${d.findings.filter(f => f.source === "manual").length} 项）。完成后将锁定本次审查${d.arm === "control" ? "，并显示 AI 意见供对比；对照组不能重新打开" : ""}。`),
      choice, note], "确认完成", async () => {
        flush();
        const c = choice.querySelector("input:checked").value;
        await api(`/api/documents/${id}/complete`, { json: { conclusion: c, summary_note: note.value } });
        S.doc = await api(`/api/documents/${id}`); shownMs = S.doc.active_ms; render(); toast("审查已完成");
      });
  }

  render();
}

function pageProcessing(app, doc) {
  app.append(h("div", { class: "page-head" }, h("a", { class: "btn ghost", href: "#/" }, "← 返回"), h("h1", {}, doc.title)));
  if (doc.status === "failed") {
    app.append(h("div", { class: "banner fail" }, h("b", {}, "AI 初审失败"), h("span", {}, doc.error || ""), h("div", { class: "spacer" }),
      h("button", { class: "btn", onclick: async () => { await api(`/api/documents/${doc.id}/retry`, { json: {} }); route(); } }, "重试")));
    return;
  }
  const steps = (doc.mode === "offline" ? [STAGES[0], STAGES[3]] : STAGES);
  const ci = steps.findIndex(s => s[0] === doc.stage);
  app.append(h("div", { class: "panel", style: "padding:24px" },
    h("div", { style: "text-align:center" }, h("b", {}, doc.status === "queued" ? "排队中…" : "AI 初审进行中"),
      h("div", { class: "muted small" }, doc.mode === "offline" ? "仅关键词，几秒完成" : "通常需要 1 分钟左右，可以先离开，完成后在列表里打开")),
    h("ol", { class: "stepper" }, ...steps.map(([k, label], i) =>
      h("li", { class: ci > i ? "done" : ci === i ? "cur" : "" }, h("span", { class: "n" }, ci > i ? "✓" : i + 1), label)))));
  const t = setTimeout(route, 2000);
  cleanup.push(() => clearTimeout(t));
}

// ---------- 弹窗 ----------
function openModal(title, body, okText, onOk) {
  closeModal();
  const ok = h("button", { class: "btn primary" }, okText);
  const bg = h("div", { class: "modal-bg", onclick: e => e.target === bg && closeModal() },
    h("div", { class: "modal", role: "dialog" }, h("div", { class: "mh" }, title), h("div", { class: "mb" }, body),
      h("div", { class: "mf" }, h("button", { class: "btn", onclick: closeModal }, "取消"), ok)));
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    try { const r = await onOk(); if (r !== false) closeModal(); } catch (e) { toast(e.message, true); }
    ok.disabled = false;
  });
  document.body.append(bg);
}
function closeModal() { document.querySelectorAll(".modal-bg").forEach(m => m.remove()); }

// ---------- 度量 ----------
async function pageMetrics(app) {
  const m = await api("/api/metrics");
  const A = m.arms.ai, C = m.arms.control;
  app.append(h("div", { class: "page-head" }, h("h1", {}, "度量"), h("span", { class: "sub" }, "只统计已完成审查的材料")));
  const enough = A.docs >= 5 && C.docs >= 5; // 样本太少时百分比会误导
  const saving = enough && C.avg_minutes ? Math.round(100 * (1 - A.avg_minutes / C.avg_minutes)) : null;
  const kpi = (v, l, d) => h("div", { class: "panel kpi" }, h("div", { class: "v" }, v ?? "—"), h("div", { class: "l" }, l), d ? h("div", { class: "d" }, d) : null);
  app.append(h("div", { class: "kpis" },
    kpi(A.avg_minutes != null ? `${A.avg_minutes} 分` : null, "AI 辅助组 平均有效用时", `${A.docs} 份 · 中位数 ${A.median_minutes ?? "—"} 分`),
    kpi(C.avg_minutes != null ? `${C.avg_minutes} 分` : null, "对照组 平均有效用时", `${C.docs} 份 · 中位数 ${C.median_minutes ?? "—"} 分`),
    kpi(saving != null ? `${saving}%` : null, "用时降幅（目标 ≥ 50%）", enough ? "两组材料不同，只看趋势" : "每组完成至少 5 份后计算"),
    kpi(A.adoption_rate != null ? `${Math.round(A.adoption_rate * 100)}%` : null, "AI 意见采纳率（含修改）", `${A.ai_findings || 0} 条意见`),
    kpi(A.manual_per_doc, "AI 组每份人工补充问题", "= 审核人认为 AI 漏报的问题")));
  const reasons = Object.entries(A.reject_reasons || {});
  const maxR = Math.max(1, ...reasons.map(r => r[1]));
  app.append(h("div", { class: "kpis", style: "grid-template-columns:repeat(auto-fit,minmax(320px,1fr))" },
    h("div", { class: "panel section" }, h("h2", {}, "AI 意见处理"), h("div", { class: "bars" },
      ...[["accepted", "采纳"], ["modified", "修改后采纳"], ["rejected", "驳回"]].map(([k, l]) => {
        const v = (A.decisions || {})[k] || 0, tot = Math.max(1, A.ai_findings || 0);
        return h("div", { class: "bar-row" }, h("span", {}, l), h("span", { class: "track" }, h("i", { style: `width:${100 * v / tot}%` })), h("span", {}, v));
      }))),
    h("div", { class: "panel section" }, h("h2", {}, "驳回原因"), reasons.length ? h("div", { class: "bars" },
      ...reasons.map(([k, v]) => h("div", { class: "bar-row" }, h("span", {}, META.reject_reasons[k] || k),
        h("span", { class: "track" }, h("i", { style: `width:${100 * v / maxR}%;background:var(--faint)` })), h("span", {}, v))))
      : h("div", { class: "note", style: "padding-top:12px" }, "暂无驳回")),
    h("div", { class: "panel section" }, h("h2", {}, "对照组：人与 AI 各自发现"), h("div", { class: "bars" },
      h("div", {}, `审核人标注问题（按 材料×规则 计）：${C.human_issue_rules ?? 0}`),
      h("div", {}, `其中 AI 也报了：${C.ai_also_found ?? 0}`),
      h("div", {}, `AI 报了、审核人没标：${C.ai_only ?? 0}`, h("span", { class: "faint small" }, "（可能是人漏审，也可能是 AI 误报，需抽查）"))))));
  if (m.per_rule.length) app.append(h("div", { class: "panel section" }, h("h2", {}, "按规则（AI 辅助组）"),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ...["规则", "AI 意见", "采纳", "驳回", "人工补充（AI 漏报）"].map(t => h("th", {}, t)))),
      h("tbody", {}, ...m.per_rule.map(r => h("tr", {}, h("td", {}, `${r.rule_id} ${r.title || ""}`), h("td", {}, r.ai), h("td", {}, r.adopted),
        h("td", {}, r.rejected), h("td", {}, r.manual ? h("span", { class: "chip manual" }, r.manual) : 0)))))));
  if (m.golden.length) app.append(h("div", { class: "panel section" }, h("h2", {}, "黄金集样例：最终结论 vs 预期标注"),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ...["材料", "样例", "组别", "预期问题", "最终漏审", "AI 漏报", "用时"].map(t => h("th", {}, t)))),
      h("tbody", {}, ...m.golden.map(g => h("tr", { class: "click", onclick: () => (location.hash = `#/doc/${g.doc_id}`) },
        h("td", {}, `#${g.doc_id}`), h("td", {}, g.case), h("td", {}, g.arm === "ai" ? "AI 辅助" : "对照组"),
        h("td", {}, g.expected.join("、") || "无"),
        h("td", {}, g.final_missed.length ? h("span", { class: "chip high" }, g.final_missed.join("、")) : h("span", { class: "chip ok" }, "无")),
        h("td", {}, g.ai_missed.join("、") || "无"), h("td", {}, `${g.minutes} 分`))))),
    h("div", { class: "note", style: "padding-top:8px" }, "“最终漏审”= 预期问题里，审核人最终没有认定（采纳/修改/人工补充）的规则；这是验证“不增加漏审”的核心指标。")));
}

// ---------- 启动 ----------
// ---------- 登录 ----------
function showLogin() {
  cleanup.forEach(f => f()); cleanup = [];
  $("#topnav").hidden = true;
  const user = h("input", { class: "input", autocomplete: "username", placeholder: "用户名" });
  const pw = h("input", { class: "input", type: "password", autocomplete: "current-password", placeholder: "口令" });
  const err = h("div", { class: "small", style: "color:var(--high);min-height:20px" });
  const btn = h("button", { class: "btn primary", type: "submit", style: "width:100%;justify-content:center" }, "登录");
  const form = h("form", { class: "panel login" }, h("div", { class: "brand", style: "margin-bottom:16px" }, h("span", { class: "logo" }, "审"), "合规初审工作台"),
    h("label", { class: "field" }, h("span", {}, "用户名"), user), h("label", { class: "field" }, h("span", {}, "口令"), pw), err, btn);
  form.addEventListener("submit", async e => {
    e.preventDefault(); btn.disabled = true; err.textContent = "";
    try { await api("/api/login", { json: { username: user.value, password: pw.value } }); await start(); }
    catch (x) { err.textContent = x.message; btn.disabled = false; }
  });
  $("#app").replaceChildren(form);
  setTimeout(() => user.focus(), 0);
}

async function start() {
  META = await api("/api/meta");
  $("#topnav").hidden = false;
  $("#whoami").textContent = `${META.user.name}${META.user.role === "admin" ? "（管理员）" : ""}`;
  route();
}

// ---------- 启动 ----------
(function init() {
  $("#logout").addEventListener("click", async () => { await api("/api/logout", { json: {} }).catch(() => {}); showLogin(); });
  window.addEventListener("hashchange", () => META.user && route());
  start().catch(() => {});
})();
