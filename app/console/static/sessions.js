import {
  state,
  has,
  product,
  request,
  post,
  patch,
  remove,
  directory,
  choices,
  main,
  active,
  go,
  on,
  notify,
  confirmAction,
  formDialog,
  bindPager,
  bindSearch,
  searchBar,
  back,
  refresh,
  permission,
  showInfo,
  awaitRun,
  techRows,
  lookupLabel,
} from "./app.js";
import {
  el,
  heading,
  esc,
  date,
  icon,
  btn,
  panel,
  empty,
  badge,
  table,
  pager,
  link,
  field,
  control,
  area,
  opt,
  workflow,
  details,
  note,
  idAttr,
  kinds,
  actions,
  reason,
  states,
  deliveryHint,
  toolNames,
} from "./ui.js";
const pth = (id) => "/sessions/" + encodeURIComponent(id),
  tpath = (id) => "/tasks/" + encodeURIComponent(id);
const actor = (x) =>
  x === "auto"
    ? "助手自动处理"
    : x === "pending"
      ? "等待人工认领"
      : !x || x === "none"
        ? "已结束"
        : x === state.ws.actor
          ? "我（当前坐席）"
          : "其他人工坐席";
const tabs = (activeTab) =>
  '<div class="tabs">' +
  [
    ["客户会话", "sessions", "sessions"],
    ["销售与接待任务", "sessions/tasks", "tasks"],
  ]
    .map(([label, h, k]) => link(label, h, k === activeTab ? "on" : ""))
    .join("") +
  "</div>";
function stateSelect(group, current) {
  return (
    '<label class="searchbox">状态 <select data-filter>' +
    opt("", "全部状态", current) +
    Object.entries(states[group])
      .map(([k, name]) => opt(k, name, current))
      .join("") +
    "</select></label>"
  );
}
export async function sessions(n, parts, isHuman = false) {
  if (parts[1] === "run") return runDetails(n, parts[2]);
  if (parts[1] === "task") return task(n, parts[2]);
  if (parts[1] === "view") return conversation(n, parts[2], isHuman);
  const taskList = parts[1] === "tasks";
  const scopedAgent = taskList && parts[2] === "by-agent" ? parts[3] : null;
  if (
    taskList
      ? !has("business", "operator", "seat", "observe")
      : !has("business", "seat", "observe")
  ) {
    main().innerHTML = permission();
    return;
  }
  let page = 1,
    q = "",
    filter = taskList && !scopedAgent ? parts[2] || "" : "",
    ticket = 0;
  async function draw() {
    const seq = ++ticket,
      group = taskList ? "task" : "session",
      kind = isHuman ? "handoffs" : taskList ? "tasks" : "sessions";
    const data = await directory(kind, {
      page,
      search: q,
      state: filter,
      agent_id: scopedAgent,
    });
    if (!active(n) || ticket !== seq) return;
    const title = isHuman ? "人工接管" : "客户会话与任务";
    let rows = data.items.map((r) =>
      taskList
        ? [
            link(
              r.task_name ||
                r.external_ref ||
                r.name +
                  " · " +
                  (r.workflow === "sales" ? "销售任务" : "客服接待"),
              "sessions/task/" + r.id,
            ),
            esc(product(r.product_id)),
            esc(r.name) + " · 第 " + r.agent_version + " 版",
            badge("task", r.status),
            link("查看任务", "sessions/task/" + r.id),
          ]
        : [
            link(r.name, (isHuman ? "handoffs" : "sessions") + "/view/" + r.id),
            esc(state.ws.tenant) +
              '<br><small class="muted">' +
              esc(r.agent) +
              "</small>",
            badge("session", r.status),
            isHuman
              ? esc(reason(r.reason))
              : '<span class="clip" title="' +
                esc(r.last_message || "") +
                '">' +
                esc(r.last_message || "暂无消息") +
                "</span>",
            esc(actor(r.owner)),
            date(r.at),
            link(
              isHuman ? "处理待办" : "查看对话",
              (isHuman ? "handoffs" : "sessions") + "/view/" + r.id,
            ),
          ],
    );
    if (isHuman) {
      rows = await Promise.all(
        data.items.map(async (r) => {
          const detail = await request(pth(r.id)),
            h = detail.handoff;
          const source =
            h?.evidence?.map((m) => m.text).join("；") || "此次交接未附原话";
          const todo =
            h?.pending?.map((m) => m.need).join("；") || "核对客户需求";
          return [
            link(r.name, "handoffs/view/" + r.id),
            '<span class="clip" title="' +
              esc(source) +
              '">' +
              esc(source) +
              '</span><small class="muted">' +
              esc(reason(h?.reason)) +
              "</small>",
            '<span>已冻结自动发送</span><small class="clip muted" title="' +
              esc(todo) +
              '">待办：' +
              esc(todo) +
              "</small>",
            badge("session", detail.status) +
              '<small class="clip muted">' +
              esc(actor(detail.owner)) +
              "</small>",
            h?.last_send ? badge("delivery", h.last_send.status) : "暂无外发",
            link("处理待办", "handoffs/view/" + r.id),
          ];
        }),
      );
      if (!active(n) || seq !== ticket) return;
    }
    main().innerHTML =
      heading(
        title,
        isHuman
          ? "查看交接缘由，认领后使用人工身份回复。"
          : scopedAgent
            ? "当前只显示这位助手的任务。"
            : "查看每位客户的聊天记录、负责方和任务进度。",
        btn("刷新列表", "reload", "重新获取工作区最新记录") +
          (!isHuman && taskList && has("business", "operator")
            ? btn(
                "创建任务",
                "new-task",
                "选取已发布的角色版本建立任务",
                "primary",
              )
            : "") +
          (!isHuman && !taskList && has("business")
            ? btn(
                "建立会话",
                "new-session",
                "绑定当前企业已核实的模拟客户",
                "primary",
              )
            : ""),
      ) +
      (isHuman ? "" : tabs(taskList ? "tasks" : "sessions")) +
      panel(
        isHuman ? "人工待办" : taskList ? "任务列表" : "客户会话",
        searchBar(q, taskList ? "搜索任务名称或助手" : "搜索客户或助手") +
          (isHuman ? "" : stateSelect(group, filter)) +
          '<div class="spaced"></div>' +
          (rows.length
            ? table(
                taskList
                  ? ["任务", "产品", "助手版本", "状态", "操作"]
                  : isHuman
                    ? [
                        "客户",
                        "原话与原因",
                        "已处理与待办",
                        "当前认领",
                        "最近发送",
                        "操作",
                      ]
                    : [
                        "客户",
                        "企业 / 助手",
                        "会话状态",
                        "最近一条消息",
                        "负责方",
                        "最近执行",
                        "操作",
                      ],
                rows,
              )
            : empty(
                isHuman ? "当前没有人工待办" : "未找到符合条件的记录",
                isHuman
                  ? "客户要求人工或触发交接规则时，会话将出现在这里。"
                  : "调整筛选条件，或从已发布的助手开始创建任务。",
              )) +
          pager(data),
      );
    bindPager(main(), data, (p) => {
      page = p;
      return draw();
    });
    bindSearch(main(), (v) => {
      q = v;
      page = 1;
      return draw();
    });
    const filterEl = el("[data-filter]");
    if (filterEl)
      filterEl.onchange = () => {
        filter = filterEl.value;
        page = 1;
        draw().catch((e) => notify(e.message));
      };
    on(main(), "reload", () => draw());
    on(main(), "new-task", () => newTask(scopedAgent));
    on(main(), "new-session", () => newSession());
  }
  await draw();
}
async function newTask(scopedAgent) {
  const a = (
    await choices("agents", scopedAgent ? { id: scopedAgent } : {})
  ).filter((x) => x.status === "published");
  if (!a.length) throw new Error("请先准备已发布的助手，再创建任务。");
  return formDialog({
    title: "创建任务",
    fields:
      field(
        "选择已发布的助手",
        '<select name="agent">' +
          a
            .map((x) =>
              opt(
                x.id,
                x.name +
                  " · " +
                  workflow(x.workflow) +
                  " · 第 " +
                  x.version +
                  " 版",
              ),
            )
            .join("") +
          "</select>",
        "任务将固定使用所选版本。",
      ) +
      field(
        "任务名称",
        control("name", "", "例如：新产品介绍", 'required maxlength="200"'),
        "便于在任务列表中辨认。",
      ) +
      field(
        "任务备注（选填）",
        area("note", "", "供同事查看的工作说明", 'maxlength="2000"'),
      ),
    submit: "创建任务",
    work: async (f) => {
      const x = a.find((x) => x.id === f.get("agent"));
      const r = await post("/tasks", {
        agent_id: x.id,
        agent_version: x.version,
        purpose: x.workflow === "sales" ? "outreach" : "support",
        name: f.get("name"),
        note: f.get("note") || null,
      });
      notify("任务已创建，等待启动");
      go("sessions/task/" + r.task_id);
    },
  });
}
async function newSession() {
  const [agents, tasks, contacts] = await Promise.all([
    choices("agents"),
    choices("tasks"),
    choices("contacts"),
  ]);
  const plans = tasks
    .filter(
      (t) =>
        ["running", "waiting"].includes(t.status) &&
        agents.some((a) => a.id === t.agent_id && a.status === "published"),
    )
    .map((t) => ({
      key: t.id,
      agent: t.agent_id,
      product: t.product_id,
      task: t.id,
      label:
        (t.task_name || t.external_ref || t.name) +
        " · 第 " +
        t.agent_version +
        " 版",
    }));
  for (const a of agents.filter(
    (a) => a.workflow === "support" && a.status === "published",
  ))
    plans.push({
      key: a.id,
      agent: a.id,
      product: a.product_id,
      label: a.name + " · 自动建立客服接待",
    });
  const options = plans.flatMap((p) =>
    contacts
      .filter((c) => !c.active_session && c.product_id === p.product)
      .map((c) => ({ p, c })),
  );
  if (!options.length)
    throw new Error(
      "暂无可绑定的客户与任务。销售任务需已启动，客户渠道须已核实且没有活动会话。",
    );
  return formDialog({
    title: "建立客户会话",
    fields: field(
      "客户与任务",
      '<select name="choice">' +
        options
          .map(({ p, c }, i) => opt(String(i), c.name + " ／ " + p.label))
          .join("") +
        "</select>",
      "只列出当前企业已经核实的绑定关系。建会话后可提交模拟来信。",
    ),
    submit: "建立会话",
    work: async (f) => {
      const { p, c } = options[Number(f.get("choice"))];
      const r = await post("/sessions", {
        agent_id: p.agent,
        customer_id: c.customer_id,
        conversation_id: c.id,
        ...(p.task ? { task_id: p.task } : {}),
      });
      notify("客户会话已建立");
      go("sessions/view/" + r.session_id);
    },
  });
}
const commands = {
  start: [
    "启动任务",
    "允许后台开始处理符合条件的工作。启动本身不会发出推销邀请。",
  ],
  pause: ["暂停任务", "立即阻止此任务继续自动发送。已受理的渠道请求无法撤回。"],
  resume: [
    "恢复任务",
    "重新处理仍符合条件的工作。已退订或已交给人工的会话继续停发。",
  ],
  stop: ["停止任务", "永久结束本任务并保留历史，之后无法恢复。"],
};
async function task(n, id) {
  const [r, d] = await Promise.all([
    request(tpath(id)),
    directory("tasks", { id }),
  ]);
  const x = d.items[0];
  if (!x) throw new Error("未找到任务。");
  if (!active(n)) return;
  const valid =
    {
      pending: ["start", "stop"],
      running: ["pause", "stop"],
      waiting: ["pause", "stop"],
      paused: ["resume", "stop"],
    }[r.status] || [];
  main().innerHTML =
    back("sessions/tasks", "返回任务列表") +
    heading(
      r.name || x.name + "的任务",
      state.ws.tenant + " · " + product(x.product_id),
    ) +
    panel(
      "任务进度",
      badge("task", r.status) +
        '<div class="spaced">' +
        techRows({
          业务类型: workflow(x.workflow),
          使用助手: x.name,
          任务备注: r.note || "无",
          固定版本: "第 " + r.agent_version + " 版",
          最近操作原因: r.reason || "尚未记录",
        }) +
        "</div>" +
        (has("business", "operator")
          ? '<div class="form-actions">' +
            valid
              .map((k) =>
                btn(
                  commands[k][0],
                  k,
                  commands[k][1],
                  k === "stop" ? "danger" : "btn",
                ),
              )
              .join("") +
            "</div>"
          : "") +
        (has("own_agents", "operator")
          ? '<div class="form-actions">' +
            btn("编辑任务", "edit-task", "修改任务名称和备注") +
            btn(
              "删除任务",
              "delete-task",
              "停止执行并从任务列表移除",
              "danger",
            ) +
            "</div>"
          : "") +
        details("高级信息", techRows({ 任务编号: id })),
    );
  const manage = "/console/manage" + tpath(id);
  on(main(), "edit-task", () =>
    formDialog({
      title: "编辑任务",
      fields:
        field(
          "任务名称",
          control("name", r.name || "", "", 'required maxlength="200"'),
        ) +
        field(
          "任务备注（选填）",
          area("note", r.note || "", "", 'maxlength="2000"'),
        ),
      submit: "保存修改",
      work: async (f) => {
        await patch(manage, {
          expected_revision: r.edit_revision,
          name: f.get("name"),
          note: f.get("note") || null,
        });
        notify("任务已更新");
        refresh();
      },
    }),
  );
  on(main(), "delete-task", () =>
    confirmAction(
      "删除任务",
      "删除“" +
        (r.name || x.name + "的任务") +
        "”会停止运行、取消待执行工作并关闭相关会话。历史记录会保留。",
      async () => {
        await remove(manage);
        notify("任务已删除");
        go("sessions/tasks");
      },
    ),
  );
  for (const k of valid)
    on(main(), k, () =>
      confirmAction(
        commands[k][0],
        commands[k][1],
        async (f) => {
          const out = await post(
            tpath(id) + "/" + k,
            k === "start" ? {} : { reason: f.get("reason") },
          );
          notify(
            "任务状态已更新为“" +
              states.task[out.status] +
              "”" +
              (out.run_id ? "，后台正在处理。" : "。"),
          );
          refresh();
        },
        k !== "start",
      ),
    );
}
function resultPanel(r) {
  const result = r.result || {};
  return (
    '<div class="mini-meta">' +
    badge("run", r.status) +
    (result.delivery ? badge("delivery", result.delivery) : "") +
    '</div><div class="spaced">' +
    techRows({
      处理结果:
        actions[result.action] ||
        (r.status === "failed" ? "执行失败" : "等待执行结果"),
      处理原因: result.reason ? reason(result.reason) : r.error?.message || "—",
      回复内容: result.text || "本轮无回复正文",
    }) +
    "</div>" +
    note(deliveryHint) +
    details(
      "工具调用与高级信息",
      techRows({
        执行编号: r.run_id,
        错误代号: r.error?.code || "无",
        工具调用: r.tool_executions?.length
          ? r.tool_executions
              .map(
                (t) =>
                  (toolNames[t.tool] || "已登记工具") +
                  " · " +
                  ({
                    denied: "已拒绝",
                    succeeded: "已完成",
                    failed: "执行失败",
                  }[t.status] || "状态待核实"),
              )
              .join("；")
          : "本轮未调用工具",
        模型调用次数: r.model_calls?.length ?? "暂未获取",
      }),
    )
  );
}
async function runDetails(n, id) {
  const r = await request("/runs/" + encodeURIComponent(id));
  if (!active(n)) return;
  main().innerHTML =
    back("overview", "返回运行总览") +
    heading(
      "执行详情",
      "查看本次执行生成的结果与消息的渠道受理状态。",
      btn("刷新结果", "reload", "重新检查这次执行的最新结果"),
    ) +
    panel(
      "本次执行",
      resultPanel(r) +
        (r.session_id
          ? link("查看客户对话", "sessions/view/" + r.session_id)
          : ""),
    );
  on(main(), "reload", refresh);
}
const factLabels = {
  seats: "账号数量",
  budget: "预算",
  language: "语言",
  role: "职位",
  needs: "需求",
};
function memInfo(m) {
  return (
    techRows({
      对话摘要: m.summary || "尚未生成",
      待跟进事项:
        m.pending
          ?.map((x) => x.need)
          .filter(Boolean)
          .join("；") || "暂无",
    }) +
    (m.facts?.length
      ? '<ul class="plain">' +
        m.facts
          .map(
            (f) =>
              "<li>" +
              esc(lookupLabel(f.key, factLabels, "客户事实")) +
              "：" +
              esc(f.value ?? "未知") +
              "</li>",
          )
          .join("") +
        "</ul>"
      : "")
  );
}
function messageCard(m) {
  const type =
    {
      inbound: "客户",
      outbound: "助手回复",
      draft: "助手草稿",
      human: "人工回复",
    }[m.kind] || "会话记录";
  return (
    '<article class="message ' +
    (m.kind === "inbound" ? "" : "out") +
    '" data-msg="' +
    esc(m.message_id) +
    '"><small>' +
    type +
    " · " +
    date(m.at) +
    "</small>" +
    (m.kind === "inbound" ? "" : badge("delivery", m.delivery)) +
    '<div><p class="bubble">' +
    esc(m.text) +
    "</p></div></article>"
  );
}
async function conversation(n, id, human) {
  const p = pth(id),
    [dir, se, mem, hist] = await Promise.all([
      directory("sessions", { id }),
      request(p),
      request(p + "/memory"),
      request(p + "/messages", { query: { limit: 100 } }),
    ]);
  const x = dir.items[0];
  if (!x) throw new Error("无法定位该客户会话。");
  const run = x.run_id
    ? await request("/runs/" + encodeURIComponent(x.run_id))
    : null;
  if (!active(n)) return;
  let cursor = hist.next_cursor;
  const h = se.handoff,
    own = se.owner === state.ws.actor,
    available = !x.task_deleted && se.status !== "ended",
    hold = ["handoff_pending", "human_owned"].includes(se.status),
    unresolved = se.unresolved_send_ids?.length;
  const name = x.name,
    orig = h?.evidence?.map((e) => e.text).join("\n") || "",
    pending = h?.pending?.map((i) => i.need).join("；");
  const order = !available
    ? ""
    : has("business") && state.ws.channel === "mock"
      ? btn("提交模拟来信", "inbound", "以已绑定的模拟客户身份提交一条客户来信")
      : "";
  const actionsHtml =
    hold && available
      ? '<div class="actions">' +
        (has("seat") && se.status === "handoff_pending"
          ? btn(
              "认领任务",
              "claim",
              "将本会话交给当前登录的人工坐席",
              "primary",
            )
          : "") +
        (has("seat") && own
          ? btn(
              "人工回复",
              "human",
              "通过统一发送入口提交人工消息",
              "primary",
              unresolved ? "disabled" : "",
            ) +
            btn(
              "交回助手",
              "release",
              "处理完成后复核条件，允许助手继续处理新消息",
            ) +
            btn(
              "结束会话",
              "close-session",
              "处理完成后结束当前会话并保留历史",
              "danger",
            )
          : "") +
        "</div>"
      : "";
  const handCard = hold
    ? panel(
        "人工处理",
        techRows({
          原始问题: orig || "此次交接未附原话，请查看完整对话",
          转人工原因: reason(h?.reason),
          助手已完成:
            se.status === "human_owned"
              ? "已冻结自动发送，交接记录已由坐席认领"
              : "已冻结自动发送并建立交接记录",
          待处理事项: pending || "核对客户需求，决定回复、交回或结束",
          当前认领人: actor(se.owner),
          最近发送状态: h?.last_send
            ? states.delivery[h.last_send.status] || "待核实"
            : "暂无发送记录",
        }) +
          note(
            unresolved
              ? "有一条发送正在核实。请等待回执，再进行人工回复。"
              : "系统已冻结自动发送。只有认领此会话的坐席可以人工回复。",
            unresolved ? "warn" : "",
          ) +
          actionsHtml +
          (human ? link("查看完整对话", "sessions/view/" + id) : "") +
          (own
            ? '<p class="help">处理完成后，选择交回助手或结束会话。</p>'
            : ""),
      )
    : "";
  main().innerHTML =
    back(
      human ? "handoffs" : "sessions",
      human ? "返回人工待办" : "返回会话列表",
    ) +
    heading(
      name,
      state.ws.tenant + " · " + x.agent + " · " + workflow(x.workflow),
      btn("刷新会话", "reload", "读取最新来信、人工归属和发送回执") + order,
    ) +
    (x.task_deleted
      ? note("所属任务已删除，聊天记录和执行结果仍可查阅。")
      : "") +
    (human ? '<div class="before">' + handCard + "</div>" : "") +
    '<div class="split"><div class="stack">' +
    panel(
      "完整对话",
      '<div class="mini-meta">' +
        badge("session", se.status) +
        esc(actor(se.owner)) +
        '</div><div class="thread">' +
        (hist.items.map(messageCard).join("") ||
          empty("还没有聊天记录", "收到客户来信或人工回复后会显示在这里。")) +
        "</div>" +
        (cursor ? btn("读取后续消息", "more", "按时间顺序读取下一页") : "") +
        '<p class="help">' +
        esc(deliveryHint) +
        "</p>",
    ) +
    (!human ? handCard : "") +
    (run ? panel("最近一次执行", resultPanel(run)) : "") +
    '</div><div class="stack">' +
    panel(
      "客户资料与记忆",
      techRows({
        客户名称: name,
        所属企业: state.ws.tenant,
        对应产品: product(x.product_id),
        资料语言:
          x.profile?.language === "zh-CN"
            ? "简体中文"
            : x.profile?.language || "未提供",
      }) +
        memInfo(mem) +
        (!x.task_deleted && has("seat") && has("correct") && hist.items.length
          ? btn(
              "纠正客户事实",
              "correct",
              "选择证据消息，保留纠正记录",
              "linkbtn",
            )
          : ""),
    ) +
    panel(
      "任务与会话",
      techRows({
        使用助手: x.agent,
        任务版本: "第 " + x.agent_version + " 版",
        任务状态: states.task[x.task_status],
        最近执行: date(x.at),
      }) +
        '<div class="form-actions">' +
        (x.task_deleted
          ? ""
          : link("查看任务", "sessions/task/" + x.task_id, "btn")) +
        (!hold && available && has("business", "seat")
          ? btn("转人工", "handoff", "冻结自动发送并建立人工待办")
          : "") +
        (!hold && available && has("business") && x.workflow === "sales"
          ? btn("安排联系", "schedule", "依照已核实的来源拟首信或安排跟进")
          : "") +
        "</div>",
    ) +
    "</div></div>";
  const loaded = [...hist.items];
  on(main(), "more", async (b) => {
    const page = await request(p + "/messages", {
      query: { after: cursor, limit: 100 },
    });
    if (!active(n)) return;
    loaded.push(...page.items);
    el(".thread").insertAdjacentHTML(
      "beforeend",
      page.items.map(messageCard).join(""),
    );
    cursor = page.next_cursor;
    if (!cursor) b.remove();
  });
  on(main(), "reload", refresh);
  on(main(), "handoff", () =>
    confirmAction(
      "转人工",
      "立即冻结自动发送，同时为这段对话建立一张人工待办。",
      async (f) => {
        const m = loaded.filter((m) => m.kind === "inbound").at(-1);
        await post(p + "/handoff", {
          reason: f.get("reason"),
          evidence_refs: m ? [m.message_id] : [],
        });
        notify("人工待办已建立");
        go("handoffs/view/" + id);
      },
      true,
    ),
  );
  on(main(), "claim", () =>
    formDialog({
      title: "认领任务",
      description: "您将成为此会话的负责坐席。自动发送保持冻结。",
      submit: "确认认领",
      work: async () => {
        await post(p + "/takeover", {
          handoff_id: se.handoff_id,
          expected_epoch: se.owner_epoch,
        });
        notify("会话已由您认领");
        refresh();
      },
    }),
  );
  on(main(), "human", () =>
    formDialog({
      title: "人工回复",
      description: "回复将通过统一发送入口核验权限并进入模拟渠道。",
      fields: field(
        "回复客户",
        area(
          "text",
          "",
          "填写要发送给客户的内容",
          'required maxlength="12000"',
        ),
      ),
      submit: "提交回复",
      work: async (f) => {
        const r = await post(p + "/human-messages", {
          expected_epoch: se.owner_epoch,
          text: f.get("text"),
        });
        const v = await awaitRun(r.run_id);
        notify(
          v.result?.delivery
            ? states.delivery[v.result.delivery] + "，请查看聊天记录。"
            : v.status === "failed"
              ? "本次回复执行失败，请检查执行详情。"
              : v.status === "unverified"
                ? "回复请求已受理，发送结果待查询。请刷新，避免重复提交。"
                : "回复请求已受理，后台仍在处理。",
        );
        refresh();
      },
    }),
  );
  for (const [action, res, label, desc] of [
    [
      "release",
      "resume",
      "交回助手",
      "重新检查退订、权限、回执和任务状态。通过后才恢复，旧草稿不会立即发出。",
    ],
    [
      "close-session",
      "close",
      "结束会话",
      "停止当前会话自动处理并保留历史记录。结束后无法从本会话继续跟进。",
    ],
  ])
    on(main(), action, () =>
      confirmAction(
        label,
        desc,
        async (f) => {
          const r = await post(p + "/release", {
            handoff_id: se.handoff_id,
            expected_epoch: se.owner_epoch,
            resolution: res,
            note: f.get("reason"),
          });
          notify("会话当前状态：" + states.session[r.status]);
          go("sessions/view/" + id);
        },
        true,
      ),
    );
  on(main(), "inbound", () =>
    formDialog({
      title: "提交模拟客户来信",
      description: "按已绑定客户的身份输入来信，助手可能生成模拟回复或转人工。",
      fields: field(
        "客户原话",
        area(
          "text",
          "",
          "例如：请提供正式报价。",
          'required maxlength="12000"',
        ),
      ),
      submit: "提交来信",
      work: async (f) => {
        const b = {
          external_message_id: "console-" + id + "-" + inboundKey,
          text: f.get("text"),
        };
        const v = await post(p + "/messages", b);
        if (v.run_id) await awaitRun(v.run_id, 5000);
        notify(
          v.run_id
            ? "来信已入库，请查看本轮执行结果。"
            : "已记录来信，当前不会自动回复。",
        );
        refresh();
      },
    }),
  );
  const inboundKey = crypto.randomUUID();
  on(main(), "correct", () =>
    formDialog({
      title: "纠正客户事实",
      description: "修改将记录原因与原始证据，原聊天记录保留。",
      fields:
        field(
          "事实名称",
          control("key", "", "例如：预算", 'required maxlength="200"'),
        ) +
        field("确认后的值", control("value", "", "例如：五万元", "required")) +
        field(
          "证据消息",
          '<select name="source">' +
            loaded.map((m) => opt(m.message_id, m.text.slice(0, 60))).join("") +
            "</select>",
        ) +
        field(
          "原因",
          control(
            "reason",
            "",
            "例如：客户刚刚确认",
            'required maxlength="200"',
          ),
        ),
      submit: "保存纠正",
      work: async (f) => {
        const key =
          Object.keys(factLabels).find((k) => factLabels[k] === f.get("key")) ||
          f.get("key");
        await patch(p + "/memory", {
          key,
          value: f.get("value"),
          source_ref: f.get("source"),
          reason: f.get("reason"),
          expected_revision: mem.revision,
        });
        notify("客户事实已更新");
        refresh();
      },
    }),
  );
  on(main(), "schedule", async () => {
    const contact = (await directory("contacts", { id: se.conversation_id }))
      .items[0];
    if (!contact?.source_ref) throw new Error("未找到此客户已核实的来源记录。");
    await formDialog({
      title: "安排联系",
      description: "引擎会再次核验联系资格。缺少许可时仅保留草稿。",
      fields:
        field(
          "联系类型",
          '<select name="kind">' +
            opt("opening", "拟定首次邀请") +
            opt("follow_up", "定时跟进") +
            "</select>",
        ) +
        field(
          "跟进时间",
          control("due", "", "", 'type="datetime-local"'),
          "选择定时跟进时必填，按本机时区。",
        ),
      submit: "确认安排",
      work: async (f) => {
        const kind = f.get("kind"),
          due = f.get("due");
        if (kind === "follow_up" && (!due || new Date(due) <= new Date()))
          throw new Error("请选择未来的跟进时间。");
        await post(p + "/actions", {
          kind,
          source_ref: contact.source_ref,
          ...(kind === "follow_up"
            ? { due_at: new Date(due).toISOString() }
            : {}),
        });
        notify("联系动作已排队");
        refresh();
      },
    });
  });
}
