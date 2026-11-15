import {
  state,
  request,
  main,
  active,
  back,
  on,
  notify,
  formDialog,
  showInfo,
  confirmAction,
} from "./app.js";
import {
  esc,
  opt,
  field,
  control,
  btn,
  note,
  panel,
  heading,
  date,
  empty,
} from "./ui.js";

const base = "/api/model-admin/v1";
const path = (id = "") =>
  base + "/connections" + (id ? "/" + encodeURIComponent(id) : "");
const statuses = {
  pending: "等待测试",
  ready: "连接可用",
  failed: "连接失败",
  disabled: "已停用",
};
const modelLabel = (name) =>
  ({
    "deepseek-flash": "DeepSeek Flash（推荐）",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
  })[name] || name;
const help =
  "只有你创建的助手可以使用这条连接。请填写自己的 DeepSeek 密钥，费用计入该密钥对应的 DeepSeek 账户。密钥加密保存，保存后无法查看。";
const secure = () => location.protocol === "https:";
function httpsHint() {
  const target = new URL(location.href);
  target.protocol = "https:";
  target.port = ["localhost", "127.0.0.1"].includes(target.hostname)
    ? "8443"
    : "";
  return (
    note("密钥只能在 HTTPS 加密工作台填写。打开后请重新登录。", "warn") +
    '<a class="link" href="' +
    esc(target.href) +
    '">打开 HTTPS 工作台</a>'
  );
}
export const options = () => request(base + "/options");
const mutate = (method, url, body, key) => request(url, { method, body, key });
const verify = (rec) =>
  mutate("POST", path(rec.id) + "/test", { expected_revision: rec.revision });

export function modelSummary(c) {
  if (!c.model_provider) return "旧版配置 · 使用 DeepSeek 前请绑定个人连接";
  return c.model_provider === "mock"
    ? "模拟模型"
    : "DeepSeek · " + modelLabel(c.model_name);
}
function statusText(rec) {
  return (
    (statuses[rec.status] || "暂未获取") +
    (rec.error_message ? " · " + rec.error_message : "")
  );
}
async function credentialForm(rec, selected) {
  if (!secure()) throw new Error("请先进入 HTTPS 工作台。");
  let key = crypto.randomUUID(),
    saved;
  const success = await formDialog({
    title: rec ? "更换密钥" : "添加 DeepSeek 连接",
    description: rec
      ? "此连接的所有助手共用新密钥。更换后需要重新验证。"
      : help,
    fields:
      (rec
        ? ""
        : field(
            "连接名称",
            control(
              "connectionName",
              "",
              (state.ws.username || "我的") + " DeepSeek 连接",
              'required maxlength="200"',
            ),
          )) +
      field(
        "DeepSeek API Key（模型密钥）",
        '<input name="api_key" type="password" required maxlength="512" autocomplete="off" spellcheck="false">',
        "请填写 DeepSeek 提供的模型密钥。工作台账号和密码仅用于网页登录。",
      ) +
      note(
        "点击后先加密保存，再查询账户可用状态和模型列表。此测试不会生成聊天内容。",
      ),
    submit: "保存并测试连接",
    work: async (data) => {
      const body = { api_key: data.get("api_key") };
      if (rec) body.expected_revision = rec.revision;
      else body.name = data.get("connectionName").trim();
      try {
        saved = await mutate(rec ? "PATCH" : "POST", path(rec?.id), body, key);
      } catch (err) {
        if (err.status && err.status < 500) key = crypto.randomUUID();
        throw err;
      } finally {
        data.delete("api_key");
        delete body.api_key;
        const input = document.querySelector('dialog [name="api_key"]');
        if (input) input.value = "";
      }
      try {
        saved = await verify(saved);
        notify(
          saved.status === "ready"
            ? "连接成功，可以在助手中使用。"
            : "密钥已保存，" + statusText(saved),
        );
      } catch {
        notify("密钥已保存，测试结果尚未确认。请在连接管理中刷新并重新测试。");
      }
    },
  });
  if (success && saved && selected) await selected(saved);
}
function refNames(list) {
  return list
    .map(
      (x) =>
        x.name +
        "（" +
        [x.draft ? "草稿" : "", ...x.versions.map((v) => "第 " + v + " 版")]
          .filter(Boolean)
          .join("、") +
        "）",
    )
    .join("、");
}
function managerHTML(items) {
  return (
    '<p class="help">' +
    esc(help) +
    "</p>" +
    (secure()
      ? btn(
          "添加 DeepSeek 连接",
          "conn-add",
          "新增并测试你自己的连接",
          "primary",
        )
      : httpsHint()) +
    (items.length
      ? items
          .map(
            (r) =>
              '<article class="connection-card" data-connection="' +
              esc(r.id) +
              '"><h3>' +
              esc(r.name) +
              "</h3><p>DeepSeek · " +
              esc(statusText(r)) +
              "</p><small>最近测试 " +
              esc(r.last_tested_at ? date(r.last_tested_at) : "尚未测试") +
              " · 可用模型 " +
              esc(r.supported_models.map(modelLabel).join("、") || "待验证") +
              '</small><div class="actions">' +
              btn("查看引用助手", "usage", "查看草稿和历史发布版本") +
              (secure()
                ? btn("修改名称", "rename", "仅你的账号可见") +
                  (r.status === "disabled"
                    ? ""
                    : btn("重新测试", "verify", "查询账户和模型列表") +
                      btn(
                        "更换密钥",
                        "rotate",
                        "轮换本连接所有助手使用的密钥",
                      ) +
                      btn(
                        "停用连接",
                        "disable-connection",
                        "停止此连接后续模型请求",
                        "danger",
                      )) +
                  btn("删除连接", "remove", "只能删除没有引用的连接", "danger")
                : "") +
              "</div></article>",
          )
          .join("")
      : empty("还没有模型连接", "可以添加你自己的第一个 DeepSeek 连接。"))
  );
}
async function mountManager(root, alive, changed) {
  const items = await request(path());
  if (!alive()) return;
  root.innerHTML = managerHTML(items);
  const redraw = async () => {
    if (alive()) await mountManager(root, alive, changed);
    await changed?.();
  };
  on(root, "conn-add", () => credentialForm(null, redraw));
  for (const card of root.querySelectorAll("[data-connection]")) {
    const r = items.find((x) => x.id === card.dataset.connection);
    const usage = () => request(path(r.id) + "/usage");
    on(card, "usage", async () =>
      showInfo(
        "引用此连接的助手",
        "<p>" + esc(refNames(await usage()) || "没有助手引用此连接。") + "</p>",
      ),
    );
    on(card, "rename", async () => {
      const ok = await formDialog({
        title: "修改连接名称",
        fields: field(
          "连接名称",
          control("name", r.name, "", 'required maxlength="200"'),
        ),
        submit: "保存名称",
        work: (f) =>
          mutate("PATCH", path(r.id), {
            expected_revision: r.revision,
            name: f.get("name").trim(),
          }),
      });
      if (ok) {
        notify("连接名称已更新");
        await redraw();
      }
    });
    on(card, "rotate", () => credentialForm(r, redraw));
    on(card, "verify", async () => {
      const updated = await verify(r);
      notify(statusText(updated));
      await redraw();
    });
    on(card, "disable-connection", async () => {
      const refs = await usage();
      const ok = await confirmAction(
        "停用连接",
        (refs.length
          ? "以下助手无法再通过此连接调用模型：" + refNames(refs) + "。"
          : "") +
          "停用后需选择其他可用连接。已提交的模型请求可能已经产生费用。",
        () =>
          mutate("POST", path(r.id) + "/disable", {
            expected_revision: r.revision,
            confirm_in_use: !!refs.length,
          }),
      );
      if (ok) {
        notify("连接已停用");
        await redraw();
      }
    });
    on(card, "remove", async () => {
      const refs = await usage();
      if (refs.length) {
        showInfo(
          "连接仍被使用",
          "<p>" +
            esc(refNames(refs)) +
            "</p><p>草稿或发布版本仍在引用，无法删除。</p>",
        );
        return;
      }
      const ok = await confirmAction(
        "删除连接",
        "删除后需要重新填写密钥才能新建连接。",
        () => mutate("DELETE", path(r.id), { expected_revision: r.revision }),
      );
      if (ok) {
        notify("连接已删除");
        await redraw();
      }
    });
  }
}
export async function connectionsPage(n) {
  if (!state.ws.manage_models)
    throw new Error("请用个人账号登录后管理自己的连接。");
  main().innerHTML =
    back("agents", "返回助手管理") +
    heading("我的模型连接", state.ws.username || "当前账号") +
    panel("DeepSeek 连接", '<div id="connection-manager"></div>');
  await mountManager(document.querySelector("#connection-manager"), () =>
    active(n),
  );
}
export function selector(c, items, explicit = false) {
  const p = c.model_provider || "legacy",
    rec = items.find((x) => x.id === c.model_connection_id);
  const providers =
    (!c.model_provider
      ? opt(
          explicit ? "" : "legacy",
          explicit ? "请选择模型" : "旧版配置（真实模型需重新绑定）",
          explicit ? "" : p,
        )
      : "") +
    opt("mock", "模拟模型", p) +
    opt("deepseek", "DeepSeek", p);
  let html = field(
    "使用的模型",
    '<select name="model_provider">' + providers + "</select>",
    "模拟模型用于演示。发布后，新任务使用这里选择的模型。",
  );
  if (p !== "deepseek") return html;
  const opts =
    opt("", "请选择你已验证的个人连接", c.model_connection_id || "") +
    (c.model_connection_id && !rec
      ? opt(
          c.model_connection_id,
          "原连接不可用，请更换或重新验证",
          c.model_connection_id,
        )
      : "") +
    items.map((x) => opt(x.id, x.name, c.model_connection_id)).join("");
  html += field(
    "模型连接",
    '<select name="model_connection_id">' + opts + "</select>",
    help,
  );
  if (rec)
    html += field(
      "模型名称",
      '<select name="model_name">' +
        rec.supported_models
          .map((x) => opt(x, modelLabel(x), c.model_name || rec.default_model))
          .join("") +
        "</select>",
    );
  if (state.ws.manage_models)
    html +=
      (secure()
        ? btn("添加 DeepSeek 连接", "credential-new", "保存并测试你自己的密钥")
        : httpsHint()) +
      '<details id="inline-connections"><summary>管理已有连接</summary><div class="spaced"></div></details>';
  html += note(
    "当前助手使用真实 DeepSeek API，模型请求可能产生费用。消息渠道仍为模拟渠道。",
    "warn",
  );
  return html;
}
export function bindSelector(root, c, sync, draw, itemsChanged) {
  const sel = root.querySelector('[name="model_provider"]');
  if (!sel) return;
  sel.onchange = () => {
    sync();
    c.model_provider = sel.value;
    delete c.model_name;
    delete c.model_connection_id;
    draw();
  };
  const connection = root.querySelector('[name="model_connection_id"]');
  if (connection)
    connection.onchange = () => {
      sync();
      c.model_connection_id = connection.value;
      delete c.model_name;
      draw();
    };
  const model = root.querySelector('[name="model_name"]');
  if (model)
    model.onchange = () => {
      c.model_name = model.value;
    };
  on(root, "credential-new", async () => {
    sync();
    await credentialForm(null, async (saved) => {
      await itemsChanged();
      if (!root.isConnected) return;
      if (saved.status === "ready") {
        c.model_connection_id = saved.id;
        c.model_name = saved.default_model;
      }
      draw();
      if (saved.status !== "ready") {
        const d = document.querySelector("#inline-connections");
        if (d) d.open = true;
      }
    });
  });
  const details = root.querySelector("#inline-connections");
  if (details)
    details.ontoggle = async () => {
      if (details.open) {
        try {
          await mountManager(
            details.querySelector("div"),
            () => details.isConnected && details.open,
            async () => {
              sync();
              await itemsChanged();
              if (root.isConnected) {
                draw();
                const d = document.querySelector("#inline-connections");
                if (d) d.open = true;
              }
            },
          );
        } catch (e) {
          notify(e.message);
        }
      }
    };
}
