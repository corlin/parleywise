/* 共享工具与状态。所有用户/模型文本一律用 textContent 写入，避免 XSS。 */
export const $ = (s, el = document) => el.querySelector(s);

export function h(tag, attrs = {}, ...kids) {
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

export const STAGES = [
  ["keyword", "关键词初筛"],
  ["triage", "模型初筛（召回优先）"],
  ["review", "模型复核（压误报）"],
  ["verify", "校验原文片段与条款"],
];
export const SEV = { high: "高", medium: "中", low: "低" };
export const META = { reject_reasons: {}, conclusions: {}, rules: [], sources: {}, user: null, key_configured: false };
export const chatModelChoices = new Map();
export const chatMemoryChoices = new Map();
export const chatProjectMemoryChoices = new Map();
export const chatMcpChoices = new Map();

let cleanup = [];
export function addCleanup(fn) { cleanup.push(fn); }
export function runCleanup() { cleanup.forEach(f => f()); cleanup = []; }

let onAuthRequired = () => {};
let onRouteRequest = () => {};
export function registerCoreHandlers({ showLogin, route }) {
  onAuthRequired = showLogin;
  onRouteRequest = route;
}
export function triggerRoute() { return onRouteRequest(); }

export async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") {
    onAuthRequired();
    throw Object.assign(new Error("请先登录"), { auth: true });
  }
  if (!r.ok) throw new Error(data.detail || `请求失败（${r.status}）`);
  return data;
}

let toastTimer;
export function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), err ? 4000 : 2000);
}

export const fmtTime = ts => ts
  ? new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
  : "—";

export const fmtDur = ms => {
  const s = Math.round((ms || 0) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export function openModal(title, body, okText, onOk) {
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

export function closeModal() {
  document.querySelectorAll(".modal-bg").forEach(m => m.remove());
}
