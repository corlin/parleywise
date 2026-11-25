import { META, api, fmtTime, h, toast } from "./core.js";

export async function pageModelCalls(app) {
  const query = new URLSearchParams((location.hash.split("?")[1] || ""));
  const conversationId = query.get("conversation_id");
  const documentId = query.get("document_id");
  const filter = conversationId && /^\d+$/.test(conversationId) ? `conversation_id=${conversationId}`
    : documentId && /^\d+$/.test(documentId) ? `document_id=${documentId}` : "";
  const rows = await api("/api/model-calls" + (filter ? `?${filter}` : ""));
  const title = conversationId ? "本会话的模型调用" : documentId ? "本材料的模型调用" : "模型调用记录";
  app.append(h("div", { class: "page-head" }, h("h1", {}, title), h("div", { class: "spacer" }),
    filter ? h("a", { class: "btn ghost", href: "#/calls" }, "查看全部") : null));
  app.append(h("p", { class: "muted call-note" }, "每条为一次网关请求尝试，失败重试会分别列出。“已响应”仅表示网关返回，不代表业务结果正确。"));
  app.append(h("p", { class: "muted call-note" }, "展示本人的会话调用及工作区共享的审查调用；最多显示最近 200 条。记录不含提示词、回复或密钥。启用此功能前的历史调用不会补录。"));
  if (!rows.length) {
    app.append(h("div", { class: "panel empty" }, h("h3", {}, "暂无调用记录"), h("p", {}, "后续模型调用完成或失败后会显示在这里。")));
    return;
  }
  const kindLabel = { chat: "对话", compress: "压缩会话", review: "合规审查" };
  const stageLabel = { triage: "初审", review: "复核" };
  for (const r of rows) {
    const task = r.kind === "review" ? h("a", { href: `#/doc/${r.document_id}` }, `材料 #${r.document_id}`)
      : h("a", { href: `#/chat/${r.conversation_id}` }, `会话 #${r.conversation_id}`);
    const token = v => v == null ? "未返回" : String(v);
    const cost = r.cost_usd == null ? "未返回" : `$${r.cost_usd.toFixed(6)}`;
    app.append(h("article", { class: "panel call-card" },
      h("div", { class: "call-head" }, h("b", {}, kindLabel[r.kind] || r.kind),
        r.stage ? h("span", { class: "chip" }, stageLabel[r.stage] || r.stage) : null,
        h("span", { class: `call-status ${r.status === "responded" ? "call-ok" : "call-failed"}` }, r.status === "responded" ? "网关已响应" : "请求失败"),
        h("div", { class: "spacer" }), h("span", { class: "muted small" }, fmtTime(r.started_at))),
      h("div", { class: "call-grid" },
        h("div", {}, h("span", {}, "任务"), task),
        h("div", {}, h("span", {}, "请求模型"), h("strong", {}, r.requested_model || "未知")),
        h("div", {}, h("span", {}, "返回模型"), h("strong", {}, r.served_model || "未返回")),
        h("div", {}, h("span", {}, "耗时 / 尝试"), h("strong", {}, `${(r.duration_ms / 1000).toFixed(1)} 秒 / 第 ${r.attempt} 次`)),
        h("div", {}, h("span", {}, "输入 / 输出 token"), h("strong", {}, `${token(r.prompt_tokens)} / ${token(r.completion_tokens)}`)),
        h("div", {}, h("span", {}, "网关费用（USD）"), h("strong", {}, cost))),
      h("div", { class: "call-id", title: r.run_id || "" }, r.run_id ? `运行 ${r.run_id}` : "无运行标识")));
  }
}

export async function pageAdminModels(app) {
  if (META.user.role !== "admin") { app.append(h("p", {}, "仅管理员可管理模型。")); return; }
  const config = await api("/api/admin/chat-models");
  const available = new Set(config.models.map(m => m.id));
  const missing = config.allowed.filter(id => !available.has(id));
  const search = h("input", { class: "input", type: "search", placeholder: "搜索网关模型", "aria-label": "搜索网关模型" });
  const selection = new Set(config.allowed.filter(id => available.has(id)));
  const defaultPicker = h("select", { class: "input", "aria-label": "默认聊天模型" });
  const list = h("div", { class: "model-admin-list" });
  const count = h("p", { class: "small muted" });
  function renderDefault() {
    const previous = defaultPicker.value || config.default;
    defaultPicker.replaceChildren(...Array.from(selection, id => h("option", { value: id }, id)));
    if (selection.has(previous)) defaultPicker.value = previous;
    count.textContent = `已选 ${selection.size} 个模型`;
  }
  function renderList() {
    const query = search.value.trim().toLowerCase();
    const matches = config.models.filter(m => m.id.toLowerCase().includes(query));
    list.replaceChildren(...matches.map(m => {
      const checkbox = h("input", { type: "checkbox" });
      checkbox.checked = selection.has(m.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selection.add(m.id); else selection.delete(m.id);
        renderDefault();
      });
      return h("label", { class: "model-admin-option" }, checkbox, h("span", {}, m.id));
    }));
    if (!matches.length) list.append(h("p", { class: "muted" }, "没有匹配的模型"));
  }
  search.addEventListener("input", renderList);
  renderDefault();
  renderList();
  const save = h("button", { class: "btn primary", type: "submit" }, "保存模型范围");
  const form = h("form", { class: "panel section model-admin" },
    h("p", { class: "muted" }, "勾选用户可在聊天中使用的模型，并设置默认模型。保存后立即生效。"),
    missing.length ? h("p", { class: "small" }, `以下已配置模型当前不在网关列表中，保存后将移除：${missing.join("、")}`) : null,
    search, count, list, h("label", { class: "field" }, h("span", {}, "默认模型"), defaultPicker), save);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    save.disabled = true;
    try {
      await api("/api/admin/chat-models", { method: "PUT", json: { allowed: Array.from(selection), default: defaultPicker.value } });
      toast("模型范围已保存");
    } catch (x) { toast(x.message, true); }
    finally { save.disabled = false; }
  });
  app.append(h("div", { class: "page-head" }, h("h1", {}, "模型管理")), form);
}
