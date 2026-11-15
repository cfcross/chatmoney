import { el, esc, btn, field, control, icon, note } from "./ui.js";
export const state = { ws: null, generation: 0, view: 0, clock: 0 };
export const has = (...roles) => roles.some((r) => state.ws?.roles.includes(r));
export const product = (id) =>
  state.ws?.products.find((p) => p.id === id)?.name || "所属产品暂不可见";
export const route = () =>
  (location.hash.slice(1) || "overview").split("/").map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return "";
    }
  });
export const go = (dest) => {
  if (location.hash === "#" + dest) refresh();
  else location.hash = dest;
};
export const refresh = () => window.dispatchEvent(new Event("console:refresh"));
export const main = () => el("#content");
export const active = (n) => n === state.view;
const pending = new Map();
let controller = new AbortController(),
  timer;
export function resetIdentity() {
  controller.abort();
  controller = new AbortController();
  const dialog = document.querySelector("dialog[open]");
  if (dialog) dialog.close();
  state.ws = null;
  state.generation++;
  state.view++;
  pending.clear();
}
export class Failure extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
const errors = {
  FORBIDDEN: "当前身份没有这项操作权限。请联系本企业的管理员或切换身份。",
  UNAUTHENTICATED: "登录已过期，请重新输入账号和密码。",
  NOT_FOUND: "记录不存在，或当前身份无权查看。请刷新列表。",
  IDEMPOTENCY_CONFLICT:
    "本次内容与先前提交的操作不一致。请核对记录后重新提交。",
  OWNER_CHANGED: "会话的负责方已变化。请重新查看会话，再决定如何处理。",
  RELEASE_BLOCKED:
    "会话仍有停发条件或待核实的发送。请检查最近回执、退订和任务状态。",
  SEND_UNRESOLVED: "上一条回复的结果仍待核实。请先核对回执，避免重复发送。",
  DOCUMENT_NOT_READY: "所选文件尚未解析完成或已撤回。请刷新资料库并重新选择。",
  INVALID_PROFILE:
    "配置尚不能发布。请核对产品、已批准的资料版本、角色与接管条件。",
  REVISION_CONFLICT:
    "已有其他人更新了这份配置。请重新打开页面，以最新版本为准。",
  PROFILE_DISABLED: "这位助手已停用。请使用其他已发布的助手。",
  CONVERSATION_BUSY: "这段渠道对话已有负责会话。请从客户列表打开原有对话。",
  RATE_LIMITED: "操作过于频繁，请稍后重试。",
  UNSUPPORTED_MEDIA: "请选择有效的 UTF-8 文本（.txt 或 .md）。",
  FILE_TOO_LARGE: "文件超过 1 GB，请选择更小的文件。",
  UNAVAILABLE: "服务暂时不可用。请核对已有记录，稍后重试。",
};
export function friendly(err) {
  if (err?.name === "AbortError") return "操作已取消";
  if (err?.code === "STATE_CONFLICT")
    return (err.message || "当前状态不允许此操作") + "。请刷新并核对当前状态。";
  if (err?.code === "VALIDATION_ERROR")
    return "字段填写有误，请检查必填项、内容长度和选择的记录。";
  return errors[err?.code] || err?.message || "暂时无法连接，请稍后重试。";
}
export function notify(msg) {
  clearTimeout(timer);
  el("#toast").textContent = msg;
  timer = setTimeout(() => (el("#toast").textContent = ""), 6500);
}
export async function request(path, { method = "GET", body, key, query, uploadSignal } = {}) {
  const read = method === "GET",
    url = new URL(
      path.startsWith("/console/") || path.startsWith("/api/model-admin/")
        ? path
        : "/api/v1" + path,
      location.origin,
    );
  for (const [k, v] of Object.entries(query || {}))
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  const generation = state.generation,
    headers = {},
    form = body instanceof FormData;
  // Match even a background tab to the account used when its form opened.
  if (state.ws?.browser_context)
    headers["X-Workspace-Context"] = state.ws.browser_context;
  let signature;
  if (!read) {
    if (!key)
      signature =
        method +
        url.pathname +
        ":" +
        (form
          ? JSON.stringify(
              [...body.entries()].map(([k, v]) => [
                k,
                v instanceof File ? [v.name, v.size, v.lastModified] : v,
              ]),
            )
          : JSON.stringify(body || {}));
    if (!key && !pending.has(signature))
      pending.set(signature, crypto.randomUUID());
    headers["Idempotency-Key"] = key || pending.get(signature);
    if (!form) headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      cache: "no-store",
      credentials: "same-origin",
      signal: uploadSignal ? AbortSignal.any([controller.signal, uploadSignal]) : controller.signal,
      body: read ? undefined : form ? body : JSON.stringify(body || {}),
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Failure(
      "NETWORK",
      "连接中断，结果尚未核实。请刷新查看，重试时沿用本次操作。",
    );
  }
  const data = await res.json().catch(() => ({}));
  if (generation !== state.generation)
    throw new DOMException("切换工作区", "AbortError");
  if (!read && (res.ok || res.status < 500)) pending.delete(signature);
  if (!res.ok) {
    const error = new Failure(
      data.error?.code,
      data.error?.message || "请求未完成",
      res.status,
    );
    if (
      (res.status === 401 || error.code === "WORKSPACE_CHANGED") &&
      state.ws
    ) {
      resetIdentity();
      refresh();
    }
    throw error;
  }
  return data.data;
}
export const post = (path, body = {}) =>
  request(path, { method: "POST", body });
export const patch = (path, body) => request(path, { method: "PATCH", body });
export const remove = (path) => request(path, { method: "DELETE" });
export const directory = (kind, q = {}) =>
  request("/console/data/directory/" + kind, { query: q });
export async function choices(kind, q = {}) {
  const items = [];
  let page = 1,
    total = 0;
  do {
    const r = await directory(kind, { ...q, limit: 100, page });
    total = r.total;
    items.push(...r.items);
    page++;
    if (!r.items.length) break;
  } while (items.length < total);
  return items;
}
export async function refreshWorkspace() {
  const data = await request("/console/data/workspace");
  if (state.ws && state.ws.browser_context !== data.browser_context) {
    resetIdentity();
    refresh();
    throw new DOMException("账号已切换", "AbortError");
  }
  state.ws = data;
  return data;
}
export function on(root, action, fn) {
  root.querySelectorAll('[data-act="' + action + '"]').forEach(
    (b) =>
      (b.onclick = async (e) => {
        e.preventDefault();
        if (b.disabled) return;
        const prev = b.innerHTML;
        b.disabled = true;
        b.setAttribute("aria-busy", "true");
        b.textContent = "处理中…";
        try {
          await fn(b, e);
        } catch (err) {
          if (err.name !== "AbortError") notify(friendly(err));
        } finally {
          if (b.isConnected) {
            b.disabled = false;
            b.removeAttribute("aria-busy");
            b.innerHTML = prev;
          }
        }
      }),
  );
}
export function dialog(title, html, init) {
  const previous = el("#dialog");
  if (previous.open) previous.close();
  // Keep delayed close events and secret cleanup attached to their original dialog.
  const d = previous.cloneNode(false);
  const body = document.createElement("div");
  body.id = "dialog-body";
  d.append(body);
  previous.replaceWith(d);
  d.setAttribute("lang", "zh-CN");
  body.innerHTML = '<h2 id="dialog-title">' + esc(title) + "</h2>" + html;
  if (init) init(d);
  d.showModal();
  return d;
}
export function formDialog({
  title,
  description = "",
  fields = "",
  submit = "确定",
  danger = false,
  cancellable = false,
  work,
}) {
  return new Promise((resolve) => {
    const d = dialog(
      title,
      (description ? "<p>" + esc(description) + "</p>" : "") +
        "<form>" +
        fields +
        '<div role="alert" class="error-text"></div><div class="actions">' +
        btn("取消", "cancel", "关闭并返回当前页面") +
        '<button type="submit" class="' +
        (danger ? "danger" : "primary") +
        '">' +
        esc(submit) +
        "</button></div></form>",
    );
    let busy = false, transfer;
    const close = () => {
      transfer?.abort();
      d.removeEventListener("close", close);
      form.querySelectorAll("input[type=password]").forEach((input) => {
        input.value = "";
      });
      d.removeEventListener("cancel", cancel);
      resolve(false);
    };
    const cancel = (e) => {
      if (busy && !cancellable) e.preventDefault();
    };
    d.addEventListener("cancel", cancel);
    d.addEventListener("close", close);
    el("[data-act=cancel]", d).onclick = () => d.close();
    const form = el("form", d);
    form.onsubmit = async (e) => {
      e.preventDefault();
      if (busy) return;
      const data = new FormData(form),
        button = el("[type=submit]", d);
      busy = true;
      button.disabled = true;
      el("[data-act=cancel]", d).disabled = !cancellable;
      button.textContent = cancellable ? "正在上传，等待服务器确认…" : "正在提交…";
      if (cancellable) transfer = new AbortController();
      el(".error-text", d).textContent = "";
      try {
        await work(data, transfer?.signal);
        resolve(true);
        d.close();
      } catch (err) {
        el(".error-text", d).textContent = friendly(err);
      } finally {
        busy = false;
        button.disabled = false;
        button.textContent = submit;
        el("[data-act=cancel]", form).disabled = false;
      }
    };
  });
}
export const confirmAction = (title, description, work, withReason = false) =>
  formDialog({
    title,
    description,
    submit: "确认" + title,
    danger: true,
    fields: withReason
      ? field(
          "操作原因",
          control("reason", "", "请简短说明原因", 'required maxlength="200"'),
          "原因会记录在本次操作中。",
        )
      : "",
    work,
  });
export function showInfo(title, html) {
  const d = dialog(
    title,
    html +
      '<div class="form-actions">' +
      btn("关闭", "close", "返回上一页") +
      "</div>",
  );
  el("[data-act=close]", d).onclick = () => d.close();
}
export function lookupLabel(value, map, fallback = "暂未获取") {
  return (
    map[value] ||
    (typeof value === "string" && /[\u3400-\u9fff]/.test(value)
      ? value
      : fallback)
  );
}
export async function awaitRun(id, wait = 12000) {
  const end = Date.now() + wait;
  let val;
  do {
    try {
      val = await request("/runs/" + encodeURIComponent(id));
    } catch (error) {
      // The write already returned a run id. A failed read must never resubmit it.
      if (error.name === "AbortError") throw error;
      return { run_id: id, status: "unverified", result: null };
    }
    if (["succeeded", "failed", "cancelled"].includes(val.status)) return val;
    await new Promise((r) => setTimeout(r, 350));
  } while (Date.now() < end);
  return val;
}
export function bindPager(root, data, load) {
  on(root, "prev", () => load(data.page - 1));
  on(root, "next", () => load(data.page + 1));
}
export function searchBar(value = "", placeholder = "按名称搜索") {
  return (
    '<form class="filters" data-search><input type="search" name="q" aria-label="' +
    esc(placeholder) +
    '" placeholder="' +
    esc(placeholder) +
    '" value="' +
    esc(value) +
    '"><button type="submit" class="btn">搜索</button></form>'
  );
}
export function bindSearch(root, fn) {
  const form = el("[data-search]", root);
  if (form)
    form.onsubmit = async (e) => {
      e.preventDefault();
      const b = el("button", form);
      if (b.disabled) return;
      b.disabled = true;
      b.textContent = "正在搜索…";
      try {
        await fn(new FormData(form).get("q").trim());
      } catch (err) {
        notify(friendly(err));
      } finally {
        b.disabled = false;
        b.textContent = "搜索";
      }
    };
}
export function showError(err) {
  if (err.name === "AbortError") return;
  main().innerHTML =
    note(friendly(err), "error") +
    btn("重新加载", "retry", "按当前身份再次读取页面");
  on(main(), "retry", refresh);
}
export function permission(message) {
  return note(
    message || "当前身份没有此页面的查看权限。请使用管理员提供的对应身份。",
    "warn",
  );
}
export function techRows(items) {
  return (
    '<dl class="kv smallkv">' +
    Object.entries(items)
      .map(
        ([key, val]) =>
          "<dt>" + esc(key) + "</dt><dd>" + esc(val ?? "暂未获取") + "</dd>",
      )
      .join("") +
    "</dl>"
  );
}
export const back = (href, label) =>
  '<div class="before"><a class="link" href="#' +
  esc(href) +
  '">' +
  icon("back", 14) +
  " " +
  esc(label) +
  "</a></div>";
