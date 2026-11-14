import { el, esc, btn, field, control, note } from "./ui.js";
import {
  dialog,
  confirmAction,
  formDialog,
  friendly,
  main,
  notify,
  state,
  request,
} from "./app.js";

const usernameField = (value = "") =>
  field(
    "账号",
    control(
      "username",
      value,
      "输入你的工作台账号",
      'autocomplete="username" autocapitalize="none" spellcheck="false" minlength="2" maxlength="64" required',
    ),
  );
const passwordField = (isNew = false) =>
  field(
    isNew ? "设置密码" : "密码",
    control(
      "password",
      "",
      isNew ? "至少 12 位" : "输入密码",
      'type="password" required maxlength="256" autocomplete="' +
        (isNew ? "new-password" : "current-password") +
        '" ' +
        (isNew ? 'minlength="12"' : ""),
    ),
  );
const authCall = (name, body = {}, method = "POST") =>
  request("/console/auth/" + name, {
    method,
    body,
    key: crypto.randomUUID(),
  });

export function getSetup() {
  const raw = location.hash.slice(1);
  if (!raw.startsWith("activate?")) return null;
  const params = new URLSearchParams(raw.slice("activate?".length));
  history.replaceState(null, "", location.pathname + location.search);
  return {
    username: params.get("account") || "",
    setup_code: params.get("code") || "",
  };
}
export function loginPage(setup, signedIn, firstLogin = null) {
  const settingPassword = !!(setup || firstLogin);
  const secure = location.protocol === "https:";
  let body;
  if (!secure) {
    const url = new URL(location.href);
    url.protocol = "https:";
    url.port = ["localhost", "127.0.0.1"].includes(url.hostname) ? "8443" : "";
    body =
      note("账号登录使用加密连接。", "warn") +
      '<a class="primary" href="' +
      esc(url.href) +
      '">打开加密工作台</a>';
  } else {
    body =
      '<form id="login" class="login">' +
      usernameField(setup?.username || firstLogin?.username) +
      passwordField(settingPassword) +
      (settingPassword
        ? field(
            "确认密码",
            control(
              "confirm",
              "",
              "再次输入新密码",
              'type="password" required maxlength="256" autocomplete="new-password"',
            ),
          )
        : "") +
      '<p role="alert" class="error-text"></p><button type="submit" class="primary">' +
      (settingPassword ? "设置密码并登录" : "登录工作台") +
      "</button></form>" +
      '<p class="help">' +
      (setup
        ? "账号和密码都可以直接输入。此设置链接只用一次。"
        : firstLogin
          ? "初始密码仅用于本次设置，请填写你自己的新密码。"
          : "成员使用管理员分配的账号和密码登录。") +
      "</p>";
    if (
      !settingPassword &&
      ["localhost", "127.0.0.1"].includes(location.hostname)
    )
      body +=
        "<details><summary>第一次设置管理员账号</summary><p>在安装 ChatMoney 的电脑上，双击项目里的「打开ChatMoney.cmd」。在打开的页面填写管理员账号和密码。</p></details>";
    if (settingPassword)
      body +=
        '<button class="linkbtn" type="button" data-act="normal-login">返回账号登录</button>';
  }
  main().innerHTML =
    '<div class="auth-page"><section class="panel auth-panel"><div class="auth-brand"><img src="/console/assets/mark.svg" width="36" height="36" alt="">ChatMoney</div>' +
    "<h1>" +
    (setup
      ? "设置登录账号和密码"
      : settingPassword
        ? "设置个人密码"
        : "登录工作台") +
    '</h1><p class="muted">' +
    (settingPassword
      ? "设置后将直接进入ChatMoney 工作台。"
      : "使用个人账号进入ChatMoney 工作台。") +
    "</p>" +
    body +
    "</section></div>";
  main()
    .querySelector('[data-act="normal-login"]')
    ?.addEventListener("click", () => location.replace(location.pathname));
  const form = el("#login");
  if (!form) return;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("[type=submit]"),
      error = form.querySelector("[role=alert]");
    if (button.disabled) return;
    const data = new FormData(form);
    if (settingPassword && data.get("password") !== data.get("confirm")) {
      error.textContent = "两次输入的密码不一致。";
      return;
    }
    error.textContent = "";
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "正在验证…";
    try {
      const name = data.get("username"),
        supplied = data.get("password");
      const reply = await authCall(setup ? "activate" : "login", {
        username: setup?.username || firstLogin?.username || name,
        ...(settingPassword ? { new_username: name } : {}),
        password: firstLogin ? firstLogin.password : supplied,
        ...(firstLogin ? { new_password: supplied } : {}),
        ...(setup ? { setup_code: setup.setup_code } : {}),
      });
      form.reset();
      if (reply.password_change_required) {
        loginPage(null, signedIn, { username: name, password: supplied });
        el("#login [name=password]").focus();
        return;
      }
      if (firstLogin) firstLogin.password = "";
      await signedIn();
    } catch (err) {
      error.textContent = friendly(err);
      form.querySelectorAll('[type="password"]').forEach((x) => {
        x.value = "";
      });
      form.elements.password.focus();
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  };
}

async function editMyAccount(signedOut) {
  if (await editCredentials(null)) {
    signedOut();
    notify("账号已更新，请用新的登录信息重新登录。");
  }
}

export function accountMenu(signedOut) {
  const d = dialog(
    "当前账号",
    "<p>" +
      esc(state.ws.username || state.ws.tenant) +
      '</p><div class="actions">' +
      btn("修改账号和密码", "edit-self", "输入当前密码后修改自己的登录信息") +
      (state.ws.manage_members
        ? btn("成员账号", "members", "开通或重设本企业成员账号")
        : "") +
      btn("退出登录", "logout", "退出本浏览器的工作台账号", "danger") +
      btn("返回", "cancel", "返回工作台") +
      "</div>",
  );
  d.querySelector('[data-act="cancel"]').onclick = () => d.close();
  d.querySelector('[data-act="edit-self"]').onclick = () =>
    editMyAccount(signedOut);
  const members = d.querySelector('[data-act="members"]');
  if (members)
    members.onclick = async () => {
      try {
        await memberList();
      } catch (err) {
        notify(friendly(err));
      }
    };
  const logout = d.querySelector('[data-act="logout"]');
  logout.onclick = async () => {
    logout.disabled = true;
    try {
      await authCall("logout");
      d.close();
      signedOut();
    } catch (err) {
      notify(friendly(err));
    } finally {
      logout.disabled = false;
    }
  };
}

const memberStates = {
  active: "已设置密码",
  invited: "待首次设密",
  unconfigured: "尚未开通",
};

function memberRow(item, i) {
  const level = item.administrator
    ? "企业管理员"
    : item.issuer
      ? "可开通成员账号"
      : "普通成员";
  const controls = [
    item.manageable
      ? btn(
          item.username ? "修改账号和密码" : "开通账号",
          "issue-" + i,
          "修改登录名或为成员设置新密码",
        )
      : "",
    item.deletable
      ? btn(
          "删除成员账号",
          "delete-member-" + i,
          "撤销登录权限，保留助手和业务记录",
          "danger",
        )
      : "",
    item.delegatable
      ? btn(
          item.issuer ? "收回发号权限" : "允许开通账号",
          "delegate-" + i,
          item.issuer
            ? "停止该成员发放账号的权限"
            : "允许该成员为本企业的普通成员发放账号",
        )
      : "",
  ].join("");
  return (
    "<li><div><strong>" +
    esc(item.username || item.id) +
    "</strong><small>" +
    esc(level + " · " + memberStates[item.state]) +
    '</small></div><div class="actions">' +
    controls +
    "</div></li>"
  );
}

export async function memberList(standalone = false, signedOut) {
  const workspace = state.ws;
  const { items } = await request("/console/auth/members");
  if (!state.ws || workspace.browser_context !== state.ws.browser_context)
    return;
  const heading = standalone ? "<h1>成员账号</h1>" : "";
  const html =
    heading +
    "<p>" +
    esc(workspace.tenant) +
    " · 给每位成员设置独立的登录账号和密码。</p>" +
    '<div class="actions">' +
    btn("添加成员", "add", "创建本企业的普通业务账号", "primary") +
    (standalone
      ? btn(
          "修改我的账号和密码",
          "edit-self",
          "输入当前密码后修改自己的登录信息",
        ) +
        '<a class="linkbtn" href="#agents">助手管理</a>' +
        (workspace.manage_knowledge ? '<a class="linkbtn" href="#knowledge">管理资料库和文件</a>' : "") +
        btn("退出登录", "logout", "退出当前账号")
      : btn("关闭", "close", "返回工作台")) +
    "</div>" +
    '<ul class="member-list">' +
    items.map(memberRow).join("") +
    "</ul>";
  let container;
  if (standalone) {
    main().innerHTML =
      '<section class="panel accounts-panel">' + html + "</section>";
    container = main();
    container.querySelector('[data-act="edit-self"]').onclick = () =>
      editMyAccount(signedOut);
    container.querySelector('[data-act="logout"]').onclick = async (e) => {
      const button = e.currentTarget;
      button.disabled = true;
      try {
        await authCall("logout");
        signedOut();
      } catch (err) {
        notify(friendly(err));
      } finally {
        button.disabled = false;
      }
    };
  } else {
    container = dialog("成员账号", html);
    container.querySelector('[data-act="close"]').onclick = () =>
      container.close();
  }
  async function reload() {
    if (state.ws?.browser_context !== workspace.browser_context) return;
    try {
      await memberList(standalone, signedOut);
    } catch (err) {
      notify(friendly(err));
    }
  }
  container.querySelector('[data-act="add"]').onclick = () =>
    enroll(null, reload);
  items.forEach((item, i) => {
    const issue = container.querySelector('[data-act="issue-' + i + '"]');
    if (issue)
      issue.onclick = async () => {
        if (!item.username) return enroll(item, reload);
        if (await editCredentials(item))
          notify("成员账号已更新，请通知本人重新登录。");
        await reload();
      };
    const removeMember = container.querySelector(
      '[data-act="delete-member-' + i + '"]',
    );
    if (removeMember)
      removeMember.onclick = async () => {
        const ok = await confirmAction(
          "删除成员账号",
          "删除“" +
            item.username +
            "”后，原密码和登录会话立即失效。该成员的助手和任务继续由管理员管理。",
          () =>
            authCall("members/" + encodeURIComponent(item.id), {}, "DELETE"),
        );
        if (ok) notify("成员账号已删除");
        await reload();
      };
    const grant = container.querySelector('[data-act="delegate-' + i + '"]');
    if (grant)
      grant.onclick = async () => {
        const enabled = !item.issuer;
        const action = enabled ? "允许开通账号" : "收回发号权限";
        const ok = await confirmAction(
          action,
          enabled
            ? item.username +
                " 将可以为企业内其产品范围的普通成员开通账号、重设密码。其助手和模型权限保持原样。"
            : item.username +
                " 将无法继续开通账号或重设其他成员密码。已发放的账号仍能登录。",
          () =>
            authCall("members/" + encodeURIComponent(item.id) + "/issuer", {
              enabled,
            }),
        );
        if (ok) notify(action + "已生效");
        await reload();
      };
  });
}

const newPasswordInput = (required = false) =>
  control(
    "password",
    "",
    required ? "至少 12 位" : "保留原密码时留空",
    'type="password" autocomplete="new-password" minlength="12" maxlength="256"' +
      (required ? " required" : ""),
  );

async function editCredentials(member) {
  return formDialog({
    title: member ? "修改成员账号和密码" : "修改我的账号和密码",
    description:
      "可以只改账号或只改密码。保存后，此账号需要重新登录，已有助手仍归本人。",
    fields:
      usernameField(member ? member.username : state.ws.username) +
      (member
        ? ""
        : field(
            "当前密码",
            control(
              "current_password",
              "",
              "输入当前使用的密码",
              'type="password" autocomplete="current-password" maxlength="256" required',
            ),
          )) +
      field("新密码", newPasswordInput(), "修改密码时至少填写 12 位。"),
    submit: "保存修改",
    work: async (f) => {
      const body = { username: f.get("username") };
      if (f.get("password")) body.password = f.get("password");
      if (!member) body.current_password = f.get("current_password");
      await authCall(
        member ? "members/" + encodeURIComponent(member.id) : "me",
        body,
        "PATCH",
      );
    },
  });
}

async function enroll(item, reload) {
  let result;
  const teamMember = state.ws.team_workspace;
  const submitted = formDialog({
    title: item ? "开通成员账号" : "添加成员",
    description: item
      ? "沿用这个身份原有的产品和操作权限。"
      : "请为每位成员设置自己的登录账号。",
    fields:
      usernameField(item?.username || item?.id || "") +
      field(
        "交给成员的内容",
        '<select name="delivery">' +
          (teamMember
            ? '<option value="password">直接设置登录密码</option>'
            : "") +
          '<option value="temporary_password">系统生成初始密码</option>' +
          (teamMember
            ? ""
            : '<option value="password">直接设置登录密码</option>') +
          '<option value="link">由成员自行设置密码</option></select>',
      ) +
      "<div data-password-field>" +
      field(
        "成员登录密码",
        newPasswordInput(),
        "请私下将账号和密码交给成员。成员登录后可以修改。",
      ) +
      "</div>",
    submit: "确认发放",
    work: async (f) => {
      result = await authCall("members", {
        username: f.get("username"),
        delivery: f.get("delivery"),
        ...(f.get("delivery") === "password"
          ? { password: f.get("password") }
          : {}),
        ...(item ? { existing_actor: item.id } : {}),
      });
    },
  });
  const form = el("dialog[open] form");
  const mode = form.elements.delivery;
  const pwd = form.elements.password;
  function setMode() {
    const direct = mode.value === "password";
    pwd.closest("[data-password-field]").hidden = !direct;
    pwd.required = direct;
    pwd.disabled = !direct;
    if (!direct) pwd.value = "";
  }
  mode.onchange = setMode;
  setMode();
  const ok = await submitted;
  if (!ok) {
    await reload();
    return;
  }
  if (!result.initial_password && !result.setup_code) {
    await reload();
    notify("账号已开通，成员现在可以用分配的账号和密码登录。");
    return;
  }
  const initial = !!result.initial_password;
  const url = new URL("/console", location.origin);
  if (!initial)
    url.hash =
      "activate?" +
      new URLSearchParams({
        account: result.username,
        code: result.setup_code,
      });
  const secret = initial ? result.initial_password : url.href;
  const d = dialog(
    initial ? "账号已开通，可分发给成员" : "成员的设置密码链接",
    "<p>请私下交给 <strong>" +
      esc(result.username) +
      "</strong>。" +
      (initial
        ? "初始密码仅在这里显示一次，24 小时内可用于设置个人密码。"
        : "此链接 24 小时内有效，只能使用一次。") +
      "</p>" +
      field(
        initial ? "初始密码" : "专属链接",
        control(
          initial ? "initial_password" : "invite",
          "",
          "",
          'readonly autocomplete="off" spellcheck="false"',
        ),
      ) +
      '<div class="actions">' +
      btn(
        initial ? "复制账号和初始密码" : "复制链接",
        "copy",
        "复制后私下交给本人",
        "primary",
      ) +
      btn("完成", "done", "关闭并清除本次显示的内容") +
      "</div>",
  );
  const input = d.querySelector("input");
  input.value = secret;
  d.querySelector('[data-act="copy"]').onclick = async () => {
    const value = initial
      ? "账号：" + result.username + "\n初始密码：" + input.value
      : input.value;
    try {
      await navigator.clipboard.writeText(value);
      notify(initial ? "账号和初始密码已复制" : "设置链接已复制");
    } catch {
      input.select();
      notify(
        initial
          ? "请复制选中的初始密码，连同上面的账号交给成员。"
          : "请复制选中的链接，私下交给成员。",
      );
    }
  };
  d.querySelector('[data-act="done"]').onclick = () => d.close();
  d.addEventListener(
    "close",
    () => {
      input.value = "";
      d.replaceChildren();
      reload();
    },
    { once: true },
  );
}
