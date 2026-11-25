import { $, META, addCleanup, api, chatMcpChoices, chatMemoryChoices, chatModelChoices, chatProjectMemoryChoices, fmtTime, h, openModal, toast, triggerRoute } from "./core.js";

export async function refreshChatHistory() {
  const target = $("#chat-history");
  if (!target || !META.user) return;
  try {
    const items = await api("/api/conversations");
    target.replaceChildren(...(items.length ? items.slice(0, 20).map(c => h("a", {
      href: `#/chat/${c.id}`,
      class: location.hash === `#/chat/${c.id}` ? "history-item active" : "history-item",
      title: c.title,
    }, c.title)) : [h("div", { class: "history-empty" }, "对话会保存在这里")]));
  } catch (e) {
    if (!e.auth) target.replaceChildren(h("div", { class: "history-empty" }, "无法加载对话"));
  }
}

export async function pageChat(app) {
  const match = location.hash.match(/^#\/chat\/(\d+)/);
  let id = match ? +match[1] : null;
  const convo = id ? await api(`/api/conversations/${id}`) : null;
  const [personalMemories, projectMemories, mcpServers] = await Promise.all([
    api("/api/memories"),
    api("/api/project-memories"),
    api("/api/mcp-servers"),
  ]);
  let modelList, modelError;
  try { modelList = await api("/api/chat-models"); }
  catch (e) { if (e.auth) throw e; modelError = e.message; modelList = { models: [], default: null }; }
  const stage = h("div", { class: "chat-stage" });
  const toolbar = h("div", { class: "chat-toolbar" },
    h("div", {}, h("div", { class: "chat-heading" }, convo ? convo.title : "新对话"),
      h("div", { class: "chat-subheading" }, "Parleywise 助手")),
    h("div", { class: "spacer" }));
  if (convo) {
    toolbar.append(h("a", { class: "btn ghost", href: `#/calls?conversation_id=${id}` }, "模型调用"));
    toolbar.append(h("button", { class: "btn ghost", type: "button", onclick: async e => {
      e.currentTarget.disabled = true; e.currentTarget.textContent = "压缩中…";
      try {
        const result = await api(`/api/conversations/${id}/compress`, { json: {} });
        const left = result.messages.filter(m => m.id > result.summary_through).length;
        toast(left ? `已压缩一部分，还剩 ${left} 条可继续压缩` : "会话已压缩，原始消息仍保留");
        await triggerRoute();
      } catch (x) {
        toast(x.message, true);
        e.currentTarget.disabled = false;
        e.currentTarget.textContent = "压缩会话";
      }
    } }, "压缩会话"), h("button", { class: "btn ghost", type: "button", onclick: async () => {
      if (!confirm("删除这段会话、原始消息及会话摘要？个人和项目记忆不会删除。")) return;
      try { await api(`/api/conversations/${id}`, { method: "DELETE" }); location.hash = "#/chat"; refreshChatHistory(); }
      catch (x) { toast(x.message, true); }
    } }, "删除"));
  }
  const thread = h("div", { class: "chat-thread", "aria-live": "polite" });
  if (!convo || !convo.messages.length) {
    thread.append(h("div", { class: "chat-welcome" }, h("div", { class: "welcome-logo", "aria-hidden": "true" }, "P"),
      h("div", { class: "welcome-eyebrow" }, "YOUR AI WORKSPACE"),
      h("h1", {}, `你好，${META.user.name}`), h("p", {}, "从一个问题开始，或选择一个工作任务。"),
      h("div", { class: "starter-grid" },
        h("a", { class: "starter-card", href: "#/new" }, h("span", { class: "starter-icon", "aria-hidden": "true" }, "↗"), h("b", {}, "审查营销材料"), h("span", {}, "提交材料并逐条复核 AI 意见")),
        h("a", { class: "starter-card", href: "#/documents" }, h("span", { class: "starter-icon", "aria-hidden": "true" }, "▤"), h("b", {}, "查看审查记录"), h("span", {}, "继续处理或回顾已有材料")))));
  } else {
    if (convo.summary) {
      const covered = convo.messages.filter(m => m.id <= convo.summary_through).length;
      const editSummary = () => {
        const value = h("textarea", { class: "input", rows: 8, maxlength: 6000, "aria-label": "会话摘要" });
        value.value = convo.summary;
        openModal("编辑会话摘要", [h("p", { class: "muted" }, `摘要覆盖前 ${covered} 条消息。修改摘要不会改变原始消息。`), value], "保存", async () => {
          await api(`/api/conversations/${id}/summary`, { method: "PATCH", json: { summary: value.value, expected_summary: convo.summary, expected_through: convo.summary_through } });
          await triggerRoute();
          toast("摘要已更新");
        });
      };
      thread.append(h("details", { class: "chat-summary" }, h("summary", {}, `会话摘要 · 覆盖前 ${covered} 条消息`),
        h("p", {}, convo.summary), h("p", { class: "small muted" }, "原始消息仍在下方。后续请求使用摘要及之后的消息。"),
        h("button", { class: "btn", type: "button", onclick: editSummary }, "编辑摘要")));
    }
    for (const m of convo.messages) {
      let bodyText = m.content;
      let toolTrace = null;
      if (m.role === "assistant") {
        const tm = bodyText.match(/\n\n\[已调用工具：([^\]]+)\]$/);
        if (tm) {
          bodyText = bodyText.slice(0, tm.index);
          toolTrace = tm[1];
        }
      }
      thread.append(h("article", { class: `chat-message ${m.role === "user" ? "is-user" : "is-assistant"}` },
        h("div", { class: "message-avatar", "aria-hidden": "true" }, m.role === "user" ? "你" : "P"),
        h("div", { class: "message-content" },
          h("div", { class: "message-author" }, m.role === "user" ? "你" : "Parleywise"),
          toolTrace ? h("div", { class: "tool-trace-pill" }, `⌘ 已调用工具：${toolTrace}`) : null,
          h("div", { class: "message-text" }, bodyText))));
    }
  }
  const input = h("textarea", { rows: 2, maxlength: 8000, "aria-label": "输入消息", placeholder: "向 Parleywise 提问…" });
  const modelKey = () => `${META.user.id}:${id || "new"}`;
  const models = modelList.models;
  const modelPicker = h("select", { class: "chat-model-picker", "aria-label": "选择本次发送的模型" },
    ...(models.length ? [h("option", { value: "", disabled: true }, "请选择模型"), ...models.map(m => h("option", { value: m.id }, m.name))] : [h("option", { value: "" }, "无可用模型")]));
  modelPicker.value = "";
  const savedModel = chatModelChoices.get(modelKey());
  if (models.some(m => m.id === savedModel)) modelPicker.value = savedModel;
  else if (modelList.default) modelPicker.value = modelList.default;
  modelPicker.disabled = !models.length;
  modelPicker.addEventListener("change", () => chatModelChoices.set(modelKey(), modelPicker.value));
  const savedPersonal = chatMemoryChoices.get(modelKey()) || new Set();
  const savedProject = chatProjectMemoryChoices.get(modelKey()) || new Set();
  const savedMcp = chatMcpChoices.get(modelKey()) || new Set();
  const makeCheck = (item, label, checked) => {
    const cb = h("input", { type: "checkbox", "aria-label": label });
    cb.checked = checked;
    return [item, cb];
  };
  const personalInputs = personalMemories.map(m => makeCheck(m, `个人记忆：${m.content}`, savedPersonal.has(m.id)));
  const projectInputs = projectMemories.map(m => makeCheck(m, `项目记忆：${m.content}`, savedProject.has(m.id)));
  const mcpInputs = mcpServers.filter(s => s.tools && s.tools.length)
    .map(s => makeCheck(s, `MCP：${s.name}`, savedMcp.has(s.id)));

  const allPickers = [];
  function createPicker({ idleText, activeText, hint, groups, manageHref, manageText, onSync }) {
    const summary = h("summary", {}, idleText);
    const allCbs = groups.flatMap(g => g.entries.map(([, cb]) => cb));
    const details = h("details", { class: "memory-picker" }, summary,
      h("div", { class: "memory-picker-body" },
        h("p", { class: "small muted" }, hint),
        ...groups.map(g => h("div", { class: "memory-picker-group" },
          g.title ? h("b", {}, g.title) : null,
          ...(g.entries.length ? g.entries.map(([item, cb]) => h("label", { class: "memory-option" }, cb, g.render(item)))
            : [h("p", { class: "small muted" }, g.emptyText || "暂无可用项")]))),
        h("div", { class: "picker-foot" },
          h("a", { href: manageHref }, manageText),
          allCbs.length ? h("button", {
            class: "link-btn",
            type: "button",
            onclick: () => { allCbs.forEach(c => (c.checked = false)); sync(); },
          }, "清空") : null)));
    const sync = () => {
      const n = onSync();
      summary.textContent = n ? activeText(n) : idleText;
      details.classList.toggle("has-selection", n > 0);
    };
    allCbs.forEach(cb => cb.addEventListener("change", sync));
    details.addEventListener("toggle", () => {
      if (details.open) allPickers.forEach(p => { if (p !== details) p.open = false; });
    });
    allPickers.push(details);
    sync();
    return details;
  }

  const memoryPicker = createPicker({
    idleText: "选择本次使用的记忆",
    activeText: n => `已选 ${n} 条记忆`,
    hint: "默认不使用。勾选的条目仅用于本会话发送，并会发送至模型网关。",
    manageHref: "#/memory",
    manageText: "管理记忆",
    groups: [
      { title: "个人记忆", entries: personalInputs, emptyText: "暂无记忆", render: m => h("span", {}, m.content) },
      { title: "项目记忆 · 工作区共享", entries: projectInputs, emptyText: "暂无记忆", render: m => h("span", {}, m.content) },
    ],
    onSync: () => {
      const pIds = new Set(personalInputs.filter(([, c]) => c.checked).map(([m]) => m.id));
      const jIds = new Set(projectInputs.filter(([, c]) => c.checked).map(([m]) => m.id));
      chatMemoryChoices.set(modelKey(), pIds);
      chatProjectMemoryChoices.set(modelKey(), jIds);
      return pIds.size + jIds.size;
    },
  });

  const mcpPicker = createPicker({
    idleText: "选择 MCP 工具",
    activeText: n => `已选 ${n} 个 MCP`,
    hint: "仅显示已完成工具发现的服务。勾选后模型可在本轮按需调用只读工具。",
    manageHref: "#/mcp",
    manageText: "配置 MCP",
    groups: [{
      entries: mcpInputs,
      emptyText: "暂无可用工具，请先到 MCP 配置页测试连接",
      render: s => h("div", {}, h("div", {}, `${s.name}（${s.tools.length} 个工具）`),
        h("div", { class: "small faint" }, s.tools.map(t => t.name).join(" · "))),
    }],
    onSync: () => {
      const mIds = new Set(mcpInputs.filter(([, c]) => c.checked).map(([s]) => s.id));
      chatMcpChoices.set(modelKey(), mIds);
      return mIds.size;
    },
  });

  const closePickers = () => allPickers.forEach(p => (p.open = false));
  const onDocClick = e => { if (!allPickers.some(p => p.contains(e.target))) closePickers(); };
  const onDocKey = e => { if (e.key === "Escape") closePickers(); };
  document.addEventListener("mousedown", onDocClick);
  document.addEventListener("keydown", onDocKey);
  addCleanup(() => {
    document.removeEventListener("mousedown", onDocClick);
    document.removeEventListener("keydown", onDocKey);
  });
  const send = h("button", { class: "send-button", type: "submit" }, "发送", h("span", { "aria-hidden": "true" }, "↑"));
  send.disabled = !modelPicker.value;
  const status = h("span", { class: "composer-status", role: "status" }, modelError || (!models.length ? "网关未返回可用模型" : !modelPicker.value ? "请选择模型" : ""));
  modelPicker.addEventListener("change", () => { send.disabled = !modelPicker.value; status.textContent = ""; });
  const retryModels = !models.length ? h("button", { class: "btn ghost", type: "button", onclick: () => triggerRoute() }, "重试") : null;
  const form = h("form", { class: "composer" }, input,
    h("div", { class: "composer-footer" }, modelPicker, memoryPicker, mcpPicker, status, retryModels, h("div", { class: "spacer" }), send));
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const content = input.value.trim();
    if (!content) return;
    closePickers();
    const origin = location.hash || "#/chat";
    send.disabled = true; input.disabled = true; modelPicker.disabled = true; status.textContent = "正在思考…";
    const welcome = thread.querySelector(".chat-welcome");
    if (welcome) welcome.hidden = true;
    const pendingMsg = h("article", { class: "chat-message is-user" },
      h("div", { class: "message-avatar", "aria-hidden": "true" }, "你"),
      h("div", { class: "message-content" }, h("div", { class: "message-author" }, "你"), h("div", { class: "message-text" }, content)));
    thread.append(pendingMsg);
    thread.scrollTop = thread.scrollHeight;
    try {
      if (!id) {
        const oldKey = modelKey();
        const created = await api("/api/conversations", { json: { title: content.slice(0, 32) } });
        id = created.id;
        const newKey = modelKey();
        for (const map of [chatModelChoices, chatMemoryChoices, chatProjectMemoryChoices, chatMcpChoices]) {
          if (map.has(oldKey)) map.set(newKey, map.get(oldKey));
        }
      }
      chatModelChoices.set(modelKey(), modelPicker.value);
      await api(`/api/conversations/${id}/messages`, {
        json: {
          content,
          model: modelPicker.value,
          memory_ids: personalInputs.filter(([, c]) => c.checked).map(([m]) => m.id),
          project_memory_ids: projectInputs.filter(([, c]) => c.checked).map(([m]) => m.id),
          mcp_server_ids: mcpInputs.filter(([, c]) => c.checked).map(([s]) => s.id),
        },
      });
      await refreshChatHistory();
      if ((location.hash || "#/chat") !== origin) return;
      if (!match) location.hash = `#/chat/${id}`; else await triggerRoute();
    } catch (x) {
      pendingMsg.remove();
      if (welcome) welcome.hidden = false;
      status.textContent = "发送失败，请重试";
      toast(x.message, true);
      send.disabled = false; input.disabled = false; modelPicker.disabled = false;
      input.focus();
    }
  });
  const composerArea = h("div", { class: "composer-area" }, form,
    h("p", { class: "composer-note" }, "消息会发送至已配置的模型网关。AI 内容请结合原文和专业判断核实。"));
  stage.append(toolbar, thread, composerArea);
  app.append(stage);
  if (convo && convo.messages.length) thread.scrollTop = thread.scrollHeight;
  if (window.innerWidth > 720) input.focus();
  refreshChatHistory();
}

export async function pageMemories(app) {
  const [personal, project] = await Promise.all([api("/api/memories"), api("/api/project-memories")]);
  const section = (title, description, rows, endpoint, editable) => {
    const entry = h("textarea", { class: "input", rows: 2, maxlength: 1000, "aria-label": `添加${title}`, placeholder: "输入一条记忆…" });
    const add = h("form", { class: "memory-add" }, entry, h("button", { class: "btn primary", type: "submit" }, "添加"));
    add.addEventListener("submit", async e => {
      e.preventDefault();
      try { await api(endpoint, { json: { content: entry.value } }); await triggerRoute(); }
      catch (x) { toast(x.message, true); }
    });
    const entries = rows.map(m => {
      const edit = () => {
        const value = h("textarea", { class: "input", rows: 5, maxlength: 1000, "aria-label": `编辑${title}` });
        value.value = m.content;
        openModal(`编辑${title}`, [value], "保存", async () => {
          await api(`${endpoint}/${m.id}`, { method: "PATCH", json: { content: value.value } });
          await triggerRoute();
          toast("已保存");
        });
      };
      return h("div", { class: "memory-row" },
        h("div", { class: "memory-content" }, h("div", {}, m.content),
          h("div", { class: "small muted" }, m.author ? `添加人：${m.author} · ${fmtTime(m.created_at)}` : `手动添加 · ${fmtTime(m.created_at)}`)),
        editable ? h("div", { class: "row" }, h("button", { class: "btn ghost", type: "button", onclick: edit }, "编辑"),
          h("button", { class: "btn ghost", type: "button", onclick: async () => {
            if (!confirm(`删除这条${title}？`)) return;
            try { await api(`${endpoint}/${m.id}`, { method: "DELETE" }); await triggerRoute(); } catch (x) { toast(x.message, true); }
          } }, "删除")) : null);
    });
    return h("section", { class: "panel memory-section" }, h("h2", {}, title), h("p", { class: "muted" }, description),
      ...(entries.length ? entries : [h("p", { class: "muted" }, "还没有记忆")]),
      editable ? add : h("p", { class: "small muted" }, "仅管理员可添加或修改项目记忆。"));
  };
  app.append(h("div", { class: "page-head" }, h("h1", {}, "记忆管理")),
    h("p", { class: "muted" }, "记忆不会自动发送；每次对话可选择需要使用的条目。不要保存密钥。"),
    section("个人记忆", "仅你可见，适合保存稳定偏好。", personal, "/api/memories", true),
    section("项目记忆", "此工作区的所有用户可见，由管理员维护。当前部署对应一个共享工作区。", project, "/api/project-memories", META.user.role === "admin"));
}

export async function pageMcp(app) {
  const rows = await api("/api/mcp-servers");
  const isAdmin = META.user.role === "admin";
  const name = h("input", { class: "input", required: true, maxlength: 80, placeholder: "服务名称" });
  const url = h("input", { class: "input", required: true, type: "url", placeholder: "https://example.com/mcp" });
  const form = h("form", { class: "panel section" }, h("h2", {}, "添加 HTTP MCP 服务"),
    h("p", { class: "small muted" }, "支持无状态 Streamable HTTP MCP。请勿在地址中填写密钥或查询参数。"),
    h("label", { class: "field" }, h("span", {}, "名称"), name),
    h("label", { class: "field" }, h("span", {}, "地址"), url),
    h("button", { class: "btn primary", type: "submit" }, "保存"));
  form.addEventListener("submit", async e => {
    e.preventDefault();
    try { await api("/api/mcp-servers", { json: { name: name.value, url: url.value, transport: "http" } }); await triggerRoute(); }
    catch (x) { toast(x.message, true); }
  });

  let stdioForm = null;
  if (isAdmin) {
    const sName = h("input", { class: "input", required: true, maxlength: 80, placeholder: "本地工具服务名" });
    const cmd = h("input", { class: "input", required: true, placeholder: "如 python3" });
    const args = h("input", { class: "input", placeholder: "空格分隔参数，如 tools/compliance_mcp.py" });
    stdioForm = h("form", { class: "panel section" },
      h("div", { class: "row", style: "justify-content:space-between;margin-bottom:8px" },
        h("h2", { style: "margin:0" }, "添加本地 stdio MCP（仅管理员）"),
        h("button", {
          class: "btn sm",
          type: "button",
          onclick: async e => {
            e.currentTarget.disabled = true;
            try {
              const r = await api("/api/mcp-servers/preset-compliance", { json: {} });
              toast(`已添加合规规则 MCP（发现 ${r.tools.length} 个工具）`);
              await triggerRoute();
            } catch (x) { toast(x.message, true); e.currentTarget.disabled = false; }
          },
        }, "＋ 一键添加本地合规规则 MCP")),
      h("p", { class: "small muted" }, "stdio 服务由工作区共享，添加后所有登录用户可在对话中勾选调用。"),
      h("label", { class: "field" }, h("span", {}, "名称"), sName),
      h("label", { class: "field" }, h("span", {}, "命令"), cmd),
      h("label", { class: "field" }, h("span", {}, "参数"), args),
      h("button", { class: "btn primary", type: "submit" }, "保存 stdio 服务"));
    stdioForm.addEventListener("submit", async e => {
      e.preventDefault();
      try {
        const argList = args.value.trim() ? args.value.trim().split(/\s+/) : [];
        await api("/api/mcp-servers", { json: { name: sName.value, transport: "stdio", command: cmd.value, args: argList } });
        await triggerRoute();
      } catch (x) { toast(x.message, true); }
    });
  }

  app.append(h("div", { class: "page-head" }, h("h1", {}, "MCP 配置")), form, stdioForm,
    h("div", { class: "panel section" }, h("h2", {}, "已保存的服务"),
      ...(rows.length ? rows.map(m => {
        const canDel = m.scope === "personal" || isAdmin;
        const toolsText = m.tools && m.tools.length ? m.tools.map(t => t.name).join("、") : "未拉取工具";
        const target = m.transport === "stdio" ? `${m.command} ${(m.args || []).join(" ")}`.trim() : m.url;
        return h("div", { class: "memory-row" },
          h("div", { class: "memory-content" },
            h("div", {}, h("b", {}, m.name), " ",
              h("span", { class: "chip" }, m.transport || "http"), " ",
              h("span", { class: "chip" }, m.scope === "workspace" ? "工作区共享" : "个人")),
            h("div", { class: "small muted" }, target),
            h("div", { class: "small muted" }, `工具（${(m.tools || []).length}）：${toolsText}`)),
          h("div", { class: "row" },
            h("button", {
              class: "btn sm",
              onclick: async e => {
                e.currentTarget.disabled = true;
                e.currentTarget.textContent = "连接中…";
                try {
                  const res = await api(`/api/mcp-servers/${m.id}/tools`, { json: {} });
                  toast(`连接成功，发现 ${res.tools.length} 个工具`);
                  await triggerRoute();
                } catch (x) {
                  toast(x.message, true);
                  e.currentTarget.disabled = false;
                  e.currentTarget.textContent = "刷新工具";
                }
              },
            }, "刷新工具"),
            canDel ? h("button", {
              class: "btn ghost",
              onclick: async () => {
                try { await api(`/api/mcp-servers/${m.id}`, { method: "DELETE" }); await triggerRoute(); }
                catch (x) { toast(x.message, true); }
              },
            }, "删除") : null));
      }) : [h("p", { class: "muted" }, "尚未配置")])));
}
