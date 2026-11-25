/* Parleywise 前端入口：原生 ES Modules，无构建步骤。 */
import { $, META, api, h, registerCoreHandlers, runCleanup } from "./js/core.js";
import { pageProfile, registerStartHandler, showLogin } from "./js/auth.js";
import { pageDoc, pageList, pageMetrics, pageNew } from "./js/review.js";
import { pageChat, pageMcp, pageMemories, refreshChatHistory } from "./js/chat.js";
import { pageAdminModels, pageModelCalls } from "./js/admin.js";

async function route() {
  runCleanup();
  const hash = location.hash || "#/chat";
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active",
    a.dataset.nav === "list"
      ? ["#/documents", "#/doc/", "#/new"].some(x => hash.startsWith(x))
      : a.dataset.nav === "chat"
        ? hash === "#/" || hash.startsWith("#/chat")
        : hash.startsWith(`#/${a.dataset.nav}`)));
  const app = $("#app");
  app.replaceChildren();
  try {
    let m;
    if ((m = hash.match(/^#\/doc\/(\d+)/))) await pageDoc(app, +m[1]);
    else if (hash.startsWith("#/new")) await pageNew(app);
    else if (hash.startsWith("#/metrics")) await pageMetrics(app);
    else if (hash.startsWith("#/calls")) await pageModelCalls(app);
    else if (hash.startsWith("#/profile")) await pageProfile(app);
    else if (hash.startsWith("#/chat")) await pageChat(app);
    else if (hash.startsWith("#/memory")) await pageMemories(app);
    else if (hash.startsWith("#/mcp")) await pageMcp(app);
    else if (hash.startsWith("#/models")) await pageAdminModels(app);
    else if (hash.startsWith("#/documents")) await pageList(app);
    else await pageChat(app);
  } catch (e) {
    if (!e.auth) app.append(h("div", { class: "empty" }, h("h3", {}, "出错了"), e.message));
  }
  window.scrollTo(0, 0);
}

async function start() {
  Object.assign(META, await api("/api/meta"));
  $("#topnav").hidden = false;
  $("#admin-models-nav").hidden = META.user.role !== "admin";
  $("#whoami").textContent = `${META.user.name}${META.user.role === "admin" ? "（管理员）" : ""}`;
  refreshChatHistory();
  route();
}

registerCoreHandlers({ showLogin, route });
registerStartHandler(start);

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { json: {} }).catch(() => {});
  showLogin();
});
window.addEventListener("hashchange", () => META.user && route());
start().catch(() => {});
