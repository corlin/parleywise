import { $, META, api, h, runCleanup, toast } from "./core.js";

let onLoginSuccess = async () => {};
export function registerStartHandler(fn) {
  onLoginSuccess = fn;
}

export async function showLogin() {
  runCleanup();
  META.user = null;
  $("#topnav").hidden = true;
  const user = h("input", { class: "input", autocomplete: "username", placeholder: "用户名" });
  const pw = h("input", { class: "input", type: "password", autocomplete: "current-password", placeholder: "口令" });
  const err = h("div", { class: "small", style: "color:var(--high);min-height:20px" });
  const btn = h("button", { class: "btn primary", type: "submit", style: "width:100%;justify-content:center" }, "登录");
  const form = h("form", { class: "panel login" },
    h("div", { class: "brand", style: "margin-bottom:16px" }, h("span", { class: "logo" }, "P"), "Parleywise", h("span", { class: "brand-sub" }, "合规初审")),
    h("label", { class: "field" }, h("span", {}, "用户名"), user),
    h("label", { class: "field" }, h("span", {}, "口令"), pw),
    err, btn);
  form.addEventListener("submit", async e => {
    e.preventDefault(); btn.disabled = true; err.textContent = "";
    try { await api("/api/login", { json: { username: user.value, password: pw.value } }); await onLoginSuccess(); }
    catch (x) { err.textContent = x.message; btn.disabled = false; }
  });
  $("#app").replaceChildren(form);
  const status = await fetch("/api/registration").then(r => r.json()).catch(() => ({ enabled: false }));
  if (status.enabled && form.isConnected) form.append(h("button", { class: "btn ghost", type: "button", onclick: showRegister }, "使用邀请码注册"));
  setTimeout(() => user.focus(), 0);
}

export function showRegister() {
  const username = h("input", { class: "input", required: true, autocomplete: "username", placeholder: "3–32 位字母、数字或 ._-" });
  const name = h("input", { class: "input", required: true, maxlength: 60 });
  const password = h("input", { class: "input", type: "password", required: true, minlength: 8, autocomplete: "new-password" });
  const invite = h("input", { class: "input", type: "password", required: true, autocomplete: "off" });
  const err = h("div", { class: "small", style: "color:var(--high);min-height:20px" });
  const submit = h("button", { class: "btn primary", type: "submit" }, "注册");
  const form = h("form", { class: "panel login" }, h("h1", {}, "注册账号"),
    h("p", { class: "small muted" }, "加入此工作区后可查看工作区中的材料。"),
    ...[["用户名", username], ["显示名称", name], ["口令（至少 8 位）", password], ["邀请码", invite]]
      .map(([label, input]) => h("label", { class: "field" }, h("span", {}, label), input)),
    err, submit, h("button", { class: "btn ghost", type: "button", onclick: showLogin }, "返回登录"));
  form.addEventListener("submit", async e => {
    e.preventDefault(); submit.disabled = true; err.textContent = "";
    try {
      await api("/api/register", { json: { username: username.value, name: name.value, password: password.value, invite_code: invite.value } });
      await showLogin();
      toast("注册成功，请登录");
    } catch (x) { err.textContent = x.message; submit.disabled = false; }
  });
  $("#app").replaceChildren(form);
}

export async function pageProfile(app) {
  const name = h("input", { class: "input", value: META.user.name, required: true, maxlength: 60 });
  const profile = h("form", { class: "panel section" }, h("h2", {}, "个人资料"),
    h("p", { class: "muted" }, `用户名：${META.user.username}`),
    h("label", { class: "field" }, h("span", {}, "显示名称"), name),
    h("button", { class: "btn primary", type: "submit" }, "保存"));
  profile.addEventListener("submit", async e => {
    e.preventDefault();
    try {
      META.user = await api("/api/profile", { method: "PATCH", json: { name: name.value } });
      $("#whoami").textContent = `${META.user.name}${META.user.role === "admin" ? "（管理员）" : ""}`;
      toast("已保存");
    } catch (x) { toast(x.message, true); }
  });
  const old = h("input", { class: "input", type: "password", required: true, autocomplete: "current-password" });
  const fresh = h("input", { class: "input", type: "password", required: true, minlength: 8, autocomplete: "new-password" });
  const passwords = h("form", { class: "panel section" }, h("h2", {}, "修改口令"),
    ...[["当前口令", old], ["新口令", fresh]].map(([label, input]) => h("label", { class: "field" }, h("span", {}, label), input)),
    h("p", { class: "small muted" }, "修改后所有登录会话将失效，请重新登录。"),
    h("button", { class: "btn", type: "submit" }, "修改口令"));
  passwords.addEventListener("submit", async e => {
    e.preventDefault();
    try {
      await api("/api/profile/password", { json: { old_password: old.value, new_password: fresh.value } });
      await showLogin();
      toast("口令已修改，请重新登录");
    } catch (x) { toast(x.message, true); }
  });
  app.append(h("div", { class: "page-head" }, h("h1", {}, "账号设置")), profile, passwords);
}
