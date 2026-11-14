import { el, navPages, icon, esc } from "./ui.js";
import {
  state,
  resetIdentity,
  refreshWorkspace,
  main,
  route,
  refresh,
  showError,
  has,
  notify,
} from "./app.js";
import { overview } from "./overview.js";
import { agents } from "./agents.js";
import { knowledge } from "./knowledge.js";
import { sessions } from "./sessions.js";
import {
  getSetup,
  loginPage,
  accountMenu,
  memberList,
} from "./workspace-login.js";

let setup = getSetup();
try {
  sessionStorage.removeItem("cm-console-identity");
} catch {}
const accountChanges =
  typeof BroadcastChannel === "function"
    ? new BroadcastChannel("chatmoney-workspace-login")
    : null;
let events = null,
  lastEvent = null,
  eventTimer;
function layout(key) {
  const w = state.ws;
  document.body.classList.toggle("signed-out", !w);
  el("#crumb").textContent = navPages[key][0];
  document.title = navPages[key][0] + " · ChatMoney 管理工作台";
  el("#nav").innerHTML = Object.entries(navPages)
    .filter(([page]) => page !== "members" || w?.manage_members)
    .map(
      ([k, [name, i]]) =>
        '<a class="navitem" href="#' +
        k +
        '" title="' +
        name +
        '" ' +
        (key === k ? 'aria-current="page"' : "") +
        ">" +
        icon(i) +
        name +
        "</a>",
    )
    .join("");
  el("#identity").innerHTML =
    '<div class="identity"><span class="avatar">' +
    icon("building") +
    "</span><div><strong>" +
    esc(w?.username || w?.tenant || "尚未登录") +
    "</strong><small>" +
    esc(
      w
        ? w.manage_knowledge
          ? "管理员"
          : has("business")
            ? "普通成员 · 资料只读"
            : "受限工作身份"
        : "请登录个人账号",
    ) +
    "</small></div></div>";
  el("#mode").textContent = w ? "消息渠道 · 模拟环境" : "工作区未连接";
  const sw = el("#switch");
  sw.textContent = w ? "我的账号" : "登录工作台";
  sw.title = w ? "查看账号、退出登录或管理成员" : "进入登录页";
  sw.onclick = () =>
    w
      ? accountMenu(accountChanged)
      : main().querySelector("[name=username]")?.focus();
}
function clearWorkspace() {
  stopEvents();
  resetIdentity();
  main().replaceChildren();
  history.replaceState(null, "", location.pathname + (location.hash === "#members" ? "#members" : ""));
}
function accountChanged() {
  setup = null;
  clearWorkspace();
  accountChanges?.postMessage("changed");
  refresh();
}
if (accountChanges)
  accountChanges.onmessage = () => {
    setup = null;
    clearWorkspace();
    refresh();
  };
function stopEvents() {
  if (events) events.abort();
  events = null;
  lastEvent = null;
  clearTimeout(eventTimer);
}
function startEvents() {
  if (events || !has("business", "seat", "observe")) return;
  const ctrl = new AbortController();
  events = ctrl;
  (async () => {
    while (!ctrl.signal.aborted) {
      try {
        const res = await fetch("/api/v1/events/stream", {
          headers: {
            ...(lastEvent ? { "Last-Event-ID": lastEvent } : {}),
            ...(state.ws?.browser_context
              ? { "X-Workspace-Context": state.ws.browser_context }
              : {}),
          },
          credentials: "same-origin",
          signal: ctrl.signal,
        });
        if (res.status === 410) {
          lastEvent = null;
          signalUpdate();
          continue;
        }
        if (!res.ok) {
          if ([401, 409].includes(res.status) && !ctrl.signal.aborted) {
            stopEvents();
            resetIdentity();
            refresh();
            return;
          }
          if (res.status === 403) return;
          throw new Error("断线");
        }
        const reader = res.body.getReader(),
          decoder = new TextDecoder();
        let text = "";
        try {
          while (!ctrl.signal.aborted) {
            const p = await reader.read();
            if (p.done) break;
            text += decoder
              .decode(p.value, { stream: true })
              .replace(/\r/g, "");
            let cut;
            while ((cut = text.indexOf("\n\n")) >= 0) {
              const frame = text.slice(0, cut);
              text = text.slice(cut + 2);
              const id = frame.match(/^id: (.+)$/m);
              if (id) {
                lastEvent = id[1];
                signalUpdate();
              }
            }
          }
        } finally {
          reader.releaseLock();
        }
      } catch (e) {
        if (ctrl.signal.aborted) return;
      }
      if (!ctrl.signal.aborted) await new Promise((r) => setTimeout(r, 2500));
    }
  })();
}
function signalUpdate() {
  const r = route();
  if (!["overview", "handoffs", "sessions"].includes(r[0])) return;
  clearTimeout(eventTimer);
  eventTimer = setTimeout(() => {
    if (
      !el("#dialog").open &&
      !document.querySelector("[aria-busy=true]") &&
      !document.activeElement?.matches("input,select,textarea") &&
      ["overview", "handoffs", "sessions"].includes(route()[0])
    ) {
      if (route()[0] === "overview") refresh();
      else {
        const b = main().querySelector("[data-act=reload]");
        if (b) {
          b.textContent = "有新进展 · 刷新";
          b.title = "有新的执行或消息记录，点击更新当前详情";
        }
      }
    }
  }, 650);
}
async function render() {
  const [raw, ...rest] = route();
  let key = raw in navPages ? raw : "overview";
  const n = ++state.view;
  layout(key);
  if (setup || location.protocol !== "https:") {
    document.body.classList.add("signed-out");
    loginPage(setup, accountChanged);
    return;
  }
  main().innerHTML =
    '<div class="loading" role="status">正在读取工作区记录…</div>';
  try {
    await refreshWorkspace();
    if (n !== state.view) return;
    if (state.ws.team_workspace && (!location.hash || (key === "members" && !state.ws.manage_members))) {
      key = state.ws.manage_members && !has("own_agents") ? "members" : "agents";
      history.replaceState(null, "", location.pathname + "#" + key);
    }
    layout(key);
    if (key === "members") {
      if (state.ws.manage_members) await memberList(true, accountChanged);
      else {
        main().innerHTML =
          '<section class="panel accounts-panel"><h1>成员账号</h1><p>当前账号为普通成员。需要为他人开通账号时，请联系企业管理员授权。</p><div class="actions"><a class="primary" href="/console">进入我的工作区</a><button class="secondary" type="button" data-act="account">我的账号</button></div></section>';
        main().querySelector('[data-act="account"]').onclick = () =>
          accountMenu(accountChanged);
      }
      return;
    }
    startEvents();
    if (key === "overview") await overview(n);
    else if (key === "agents") await agents(n, [key, ...rest]);
    else if (key === "knowledge") await knowledge(n, [key, ...rest]);
    else await sessions(n, [key, ...rest], key === "handoffs");
  } catch (err) {
    if (n !== state.view || err.name === "AbortError") return;
    if (err.status === 401) {
      stopEvents();
      resetIdentity();
      layout(key);
      loginPage(null, accountChanged);
    } else showError(err);
  }
}
window.addEventListener("hashchange", () => {
  const invitation = getSetup();
  if (invitation) {
    stopEvents();
    resetIdentity();
    setup = invitation;
  }
  render();
});
window.addEventListener("console:refresh", render);
window.addEventListener("pagehide", stopEvents);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) refresh();
  else if (state.ws) startEvents();
});
render();
