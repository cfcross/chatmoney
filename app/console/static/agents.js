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
  techRows,
  permission,
  refresh,
} from "./app.js";
import {
  el,
  all,
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
  toolNames,
  note,
  reason,
} from "./ui.js";
import {
  modelSummary,
  options as connectionOptions,
  selector,
  bindSelector,
  connectionsPage,
} from "./connections.js";
const triggers = {
  ask_human: ["客户要求真人", "先冻结自动回复，再生成可认领的人工待办。"],
  identity_question: ["客户询问身份", "停止自动解释身份，保留原话供真人接手。"],
  formal_quote: [
    "客户要求正式报价",
    "把正式报价交给负责人，助手停止自动发送。",
  ],
  contract: ["涉及合同或付款", "涉及商务承诺时冻结发送，由负责人处理。"],
  refund: ["客户提出退款", "交给客服坐席核实订单与售后政策。"],
  complaint: ["客户提出投诉", "保留问题和上下文，请人工处理。"],
};
const toolHelp = {
  "order.lookup": "只查询已经核实属于当前客户的模拟订单。",
  "channel.reply": "允许助手申请回复，发送前仍会检查渠道权限和人工归属。",
};
const lines = (v) => (Array.isArray(v) ? v.join("\n") : "");
const list = (v) =>
  v
    .split(/\n/)
    .map((x) => x.trim())
    .filter(Boolean);
const pth = (id) => "/agents/" + encodeURIComponent(id);
const wrap = (name, hint, text) =>
  '<div class="field"><span>' +
  esc(name) +
  "</span>" +
  text +
  "<small>" +
  esc(hint) +
  "</small></div>";
const checked = (name, id, label, help, yes) =>
  '<label class="check"><input type="checkbox" name="' +
  name +
  '" value="' +
  esc(id) +
  '" ' +
  (yes ? "checked" : "") +
  "><span>" +
  esc(label) +
  "<small>" +
  esc(help) +
  "</small></span></label>";
export async function agents(n, parts) {
  if (
    parts[1] === "connections" ||
    (state.ws.manage_models && !has("config", "publish", "business"))
  )
    return connectionsPage(n);
  if (!has("config", "publish", "business")) {
    main().innerHTML = permission();
    return;
  }
  if (parts[1] === "new" || parts[1] === "edit") return editor(n, parts[2]);
  if (parts[1] === "view") return detail(n, parts[2]);
  let page = 1,
    q = "";
  async function draw() {
    const current = ++ticket,
      data = await directory("agents", { page, search: q });
    if (!active(n) || ticket !== current) return;
    const rows = data.items.map((a) => [
      link(a.name, "agents/view/" + a.id),
      ...(state.ws.team_workspace ? [] : [esc(state.ws.tenant)]),
      esc(product(a.product_id)),
      esc(workflow(a.workflow)),
      a.version ? "第 " + a.version + " 版" : "尚未发布",
      badge("agent", a.status) +
        (a.changed ? '<div class="muted">另有未发布修改</div>' : ""),
      date(a.at),
      link("详情", "agents/view/" + a.id),
    ]);
    main().innerHTML =
      heading(
        "助手管理",
        has("own_agents")
          ? "这里显示你创建的助手。点击“创建助手”设置名称、角色和产品资料。"
          : "按产品配置角色。已发布的版本可以用于新任务。",
        (state.ws.manage_models
          ? link(
              "我的模型连接",
              "agents/connections",
              "btn",
              "添加或管理你自己使用的 DeepSeek 密钥",
            )
          : "") +
          (has("config", "own_agents")
            ? link("创建助手", "agents/new", "primary", "新建产品专属助手")
            : ""),
      ) +
      panel(
        has("own_agents") ? "我的助手" : "工作区助手",
        searchBar(q, "搜索助手名称") +
          (rows.length
            ? table(
                [
                  "助手名称",
                  ...(state.ws.team_workspace ? [] : ["所属企业"]),
                  "对应产品",
                  "业务类型",
                  "当前版本",
                  "状态",
                  "最近更新",
                  "操作",
                ],
                rows,
              )
            : empty(
                "未找到助手",
                q
                  ? "试试其他名称。"
                  : "点击“创建助手”即可开始，产品资料可以稍后选择。",
                !q && has("config", "own_agents")
                  ? link("创建助手", "agents/new", "btn")
                  : "",
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
  }
  let ticket = 0;
  await draw();
}
function splitGoal(goal = "") {
  const m = goal.match(/^主要工作：([^\n]*)\n工作目标：([\s\S]*)$/);
  return m ? { duty: m[1], goal: m[2] } : { duty: "", goal };
}
function checks(a, c) {
  return [
    [!!a.name.trim() && !!a.product_id, "已填写助手名称和所属产品"],
    [
      !!(c.persona?.trim() && c.tone?.trim() && c.goal?.trim()),
      "已填写角色、沟通风格和目标",
    ],
    [!!c.knowledge?.length, "已选择获批的资料版本"],
    [!!c.allowed?.length, "已说明可以回答的内容"],
    [!!c.policy_ref, "已关联当前业务的接管规则"],
    [
      c.model_provider !== "deepseek" || !!c.model_connection_id,
      "已选择模型连接",
    ],
  ];
}
const checklist = (tests) =>
  '<ul class="checklist">' +
  tests
    .map(
      ([ok, name]) =>
        '<li class="' +
        (ok ? "ok" : "bad") +
        '">' +
        icon(ok ? "check" : "info", 15) +
        esc(ok ? name : name.replace(/^已/, "请")) +
        "</li>",
    )
    .join("") +
  "</ul>";
async function detail(n, id) {
  const rec = (await directory("agents", { id })).items[0];
  if (!rec) throw new Error("当前身份无法查看这位助手。");
  const view = rec.version
    ? await request(pth(id))
    : await request(pth(id), { query: { view: "draft" } });
  const draft = has("config", "publish", "own_agents")
    ? await request(pth(id), { query: { view: "draft" } })
    : null;
  const c = view.config,
    allBooks = await choices("books", { product: rec.product_id });
  const connections =
    c.model_provider === "deepseek" && state.ws.manage_models
      ? await request("/api/model-admin/v1/connections")
      : [];
  const currentConnection = connections.find(
    (x) => x.id === c.model_connection_id,
  );
  if (!active(n)) return;
  main().innerHTML =
    back("agents", "返回助手列表") +
    heading(
      view.name,
      workflow(view.workflow) + " · " + product(view.product_id),
      (has("config", "own_agents") && rec.status !== "disabled"
        ? link(
            "编辑配置",
            "agents/edit/" + id,
            "btn",
            "编辑工作草稿，不影响当前任务",
          )
        : "") +
        (draft &&
        has("publish", "own_agents") &&
        (draft.model_editable || draft.config.model_provider !== "deepseek") &&
        rec.status !== "disabled" &&
        (!rec.version || rec.changed)
          ? btn("检查并发布", "publish", "查看草稿配置并发布新版本", "primary")
          : "") +
        (has("publish", "own_agents") && rec.status !== "disabled"
          ? btn("停用助手", "disable", "阻止该助手的新旧自动执行", "danger")
          : "") +
        (has("publish", "own_agents")
          ? btn(
              "删除助手",
              "delete-agent",
              "移除助手及其任务，保留聊天记录",
              "danger",
            )
          : ""),
    ) +
    '<div class="split"><div class="stack">' +
    panel(
      "角色配置",
      techRows({
        展示版本: view.version
          ? "已发布 · 第 " + view.version + " 版"
          : "工作草稿",
        使用的模型: modelSummary(c),
        ...(view.model_needs_rebind
          ? {
              模型设置:
                "旧连接属于其他账号，请在角色设置中选择自己的连接并重新发布。",
            }
          : {}),
        ...(currentConnection ? { 我的连接: currentConnection.name } : {}),
        消息渠道: "模拟渠道",
        角色: c.persona,
        沟通风格: c.tone,
        工作目标: c.goal,
        答复范围: (c.allowed || []).join("、") || "未设置",
        禁止承诺: (c.forbidden || []).join("、") || "未设置",
      }),
    ) +
    panel(
      "资料和工具",
      techRows({
        知识资料:
          (c.knowledge || [])
            .map(
              (ref) =>
                (allBooks.find((k) => k.id === ref.kb_id)?.name ||
                  "历史资料库") +
                " · 第 " +
                ref.snapshot +
                " 版",
            )
            .join("、") || "尚未选择",
        可用工具:
          (c.tool_allowlist || [])
            .map((k) => toolNames[k] || "其他工具")
            .join("、") || "未开放工具",
        转人工条件:
          (c.handoff_on || [])
            .map((k) => triggers[k]?.[0] || "其他条件")
            .join("、") || "未设置",
      }),
    ) +
    '</div><div class="stack">' +
    panel(
      "发布状态",
      badge("agent", rec.status) +
        (rec.changed
          ? note("另有未发布修改。点击“编辑配置”查看工作草稿。")
          : "") +
        '<p class="aside-copy spaced">每个新任务固定使用创建时的版本。停用会同时阻止该助手已有任务继续自动执行。</p>' +
        (rec.version > 0 && has("operator", "business")
          ? link(
              "前往任务管理",
              "sessions/tasks/by-agent/" + encodeURIComponent(id),
              "btn",
              "查看此助手的任务或创建新任务",
            )
          : ""),
    ) +
    "</div></div>";
  on(main(), "publish", () =>
    publishCheck(id, draft, () => go("agents/view/" + id)),
  );
  on(main(), "delete-agent", () =>
    confirmAction(
      "删除助手",
      "删除“" +
        view.name +
        "”将一并停止并移除该助手的所有任务。已有对话和执行记录保留。",
      async () => {
        await remove("/console/manage/agents/" + encodeURIComponent(id));
        notify("助手及其任务已删除");
        go("agents");
      },
    ),
  );
  on(main(), "disable", () =>
    confirmAction(
      "停用助手",
      "停用后，已有任务和新任务均无法使用这位助手自动执行。历史对话和发布版本可以继续查看。",
      async (f) => {
        await post(pth(id) + "/disable", { reason: f.get("reason") });
        notify("助手已停用");
        refresh();
      },
      true,
    ),
  );
}
async function publishCheck(id, draft, done) {
  const tests = checks(draft, draft.config);
  const books = await choices("books", { product: draft.product_id });
  tests.push([
    (draft.config.knowledge || []).every((ref) =>
      books.some(
        (k) =>
          k.id === ref.kb_id && k.available_versions.includes(ref.snapshot),
      ),
    ),
    "已核对绑定资料仍可使用",
  ]);
  if (tests.some((x) => !x[0])) {
    notify("配置还不完整，请编辑后再发布。");
    return;
  }
  await formDialog({
    title: "发布助手",
    description: "新任务可以选择即将发布的版本。已有任务继续按原版本运行。",
    fields: checklist(tests),
    submit: "确认发布",
    work: async () => {
      const r = await post(pth(id) + "/publish", {
        expected_revision: draft.draft_revision,
      });
      notify("已发布第 " + r.version + " 版");
      done();
    },
  });
}
async function editor(n, id) {
  if (!has("config", "own_agents")) {
    main().innerHTML = permission("创建和编辑助手需要配置管理权限。");
    return;
  }
  const ws = state.ws;
  let draft = id
    ? await request(pth(id), { query: { view: "draft" } })
    : {
        name: "",
        workflow: "sales",
        product_id: ws.products[0]?.id || "",
        config: { model_provider: "mock" },
      };
  if (draft.status === "disabled") {
    go("agents/view/" + id);
    return;
  }
  const [library, initialConnections] = await Promise.all([
    choices("books"),
    draft.model_editable === false ? [] : connectionOptions(),
  ]);
  let connections = initialConnections;
  const eligible = library.flatMap((k) =>
    k.available_versions.map((version) => ({ ...k, version })),
  );
  if (!active(n)) return;
  let a = {
    name: draft.name,
    product_id: draft.product_id,
    workflow: draft.workflow,
  };
  let c = {
    persona: "",
    tone: "",
    allowed: [],
    forbidden: [],
    knowledge: [],
    tool_allowlist: [],
    handoff_on: [],
    ...draft.config,
    ...splitGoal(draft.config.goal),
  };
  let step = 0,
    rev = draft.draft_revision,
    existing = id,
    dirty = !id;
  const locked = () => Boolean(existing);
  const titles = ["基本信息", "角色设置", "资料和工具", "人工接管"];
  function sync() {
    const form = el("#editor");
    if (!form) return;
    const f = new FormData(form);
    if (step === 0) {
      a.name = String(f.get("name") || "").trim();
      if (!locked()) {
        const wf = f.get("workflow"),
          pid = f.get("product");
        if (wf !== a.workflow) c.handoff_on = [];
        if (pid !== a.product_id) c.knowledge = [];
        a.workflow = wf;
        a.product_id = pid;
      }
    }
    if (step === 1) {
      for (const k of ["persona", "tone", "duty", "goal"])
        c[k] = String(f.get(k) || "").trim();
    }
    if (step === 2) {
      c.knowledge = all("[name=knowledge]:checked", form).map((x) => ({
        kb_id: x.value,
        snapshot: Number(x.dataset.version),
      }));
      c.tool_allowlist = f.getAll("tools");
      c.allowed = list(String(f.get("allowed") || ""));
      c.forbidden = list(String(f.get("forbidden") || ""));
    }
    if (step === 3) c.handoff_on = f.getAll("handoff");
  }
  function config() {
    return {
      company_ref: ws.company_ref,
      product_ref: ws.products.find((p) => p.id === a.product_id)?.version_ref,
      ...(draft.model_editable !== false && c.model_provider
        ? {
            model_provider: c.model_provider,
            model_connection_id:
              c.model_provider === "deepseek"
                ? c.model_connection_id || null
                : null,
            model_name:
              c.model_provider === "deepseek"
                ? c.model_name ||
                  connections.find((x) => x.id === c.model_connection_id)
                    ?.default_model ||
                  null
                : null,
          }
        : {}),
      ...(draft.model_editable !== false && c.model_params
        ? { model_params: c.model_params }
        : {}),
      policy_ref: ws.rules[a.workflow]?.ref,
      persona: c.persona,
      tone: c.tone,
      goal: c.duty ? "主要工作：" + c.duty + "\n工作目标：" + c.goal : c.goal,
      allowed: c.allowed,
      forbidden: c.forbidden,
      knowledge: c.knowledge,
      tool_allowlist: c.tool_allowlist,
      handoff_on: c.handoff_on,
    };
  }
  function options() {
    const opts = eligible
      .filter((k) => k?.product_id === a.product_id)
      .map((k) => ({ kb_id: k.id, snapshot: k.version, name: k.name }));
    for (const ref of c.knowledge)
      if (
        !opts.some((x) => x.kb_id === ref.kb_id && x.snapshot === ref.snapshot)
      )
        opts.push({
          ...ref,
          inactive: true,
          name:
            (library.find((k) => k.id === ref.kb_id)?.name || "历史资料库") +
            "（资料已失效，请另选）",
        });
    return opts;
  }
  function draw() {
    if (!active(n)) return;
    let body = "";
    if (step === 0)
      body =
        field(
          "助手名称",
          control("name", a.name, "例如：排班产品商务顾问", 'maxlength="200"'),
          "选一个同事能在列表中认出的名称。",
        ) +
        '<div class="halves">' +
        (ws.team_workspace
          ? ""
          : field(
              "所属企业",
              control("tenant", ws.tenant, "", "readonly"),
              "由登录身份确定。",
            )) +
        field(
          "对应产品",
          '<select name="product" ' +
            (locked() ? "disabled" : "") +
            ">" +
            ws.products.map((x) => opt(x.id, x.name, a.product_id)).join("") +
            "</select>",
          "助手只读取这款产品批准的资料。",
        ) +
        "</div>" +
        field(
          "业务类型",
          '<select name="workflow" ' +
            (locked() ? "disabled" : "") +
            ">" +
            opt("sales", "企业销售", a.workflow) +
            opt("support", "店铺客服", a.workflow) +
            "</select>",
          "销售负责商务沟通，客服处理来信。保存后产品和业务类型固定。",
        );
    if (step === 1)
      body =
        field(
          "助手扮演的角色",
          control(
            "persona",
            c.persona,
            "例如：排班产品的商务顾问",
            'maxlength="200"',
          ),
          "说明它以什么身份帮助客户。",
        ) +
        field(
          "主要负责的工作",
          control("duty", c.duty, "例如：澄清企业排班需求", 'maxlength="200"'),
          "与下方目标一同保存在工作说明中。",
        ) +
        field(
          "沟通风格",
          control("tone", c.tone, "例如：专业、简洁、亲切", 'maxlength="200"'),
          "决定助手和客户说话的方式。",
        ) +
        field(
          "希望达到的目标",
          area(
            "goal",
            c.goal,
            "例如：确认团队规模并安排演示",
            'maxlength="11000"',
          ),
          "描述一次对话希望得到的具体结果。",
        ) +
        (draft.model_editable === false
          ? note("模型由助手创建者管理。你可以维护其他角色资料。")
          : (draft.model_needs_rebind
              ? note(
                  "此助手原先引用了其他账号的连接。请选择你自己的连接，或改用模拟模型。",
                  "warn",
                )
              : "") + selector(c, connections));
    if (step === 2)
      body =
        wrap(
          "选择已批准的产品资料",
          "选择一个或多个已发布版本。更新资料后可另发助手新版本。",
          options()
            .map(
              (k) =>
                '<label class="check"><input type="checkbox" name="knowledge" value="' +
                esc(k.kb_id) +
                '" data-version="' +
                k.snapshot +
                '" ' +
                (k.inactive
                  ? "disabled"
                  : c.knowledge.some(
                        (y) => y.kb_id === k.kb_id && y.snapshot === k.snapshot,
                      )
                    ? "checked"
                    : "") +
                "><span>" +
                esc(k.name) +
                " · 第 " +
                k.snapshot +
                " 版</span></label>",
            )
            .join("") ||
            note(
              "可以先保存草稿。发布助手前，请由管理员在资料库批准并发布一份产品资料。",
              "warn",
            ),
        ) +
        wrap(
          "可使用的工具",
          "按工作需要选择。最终执行仍受账号、客户身份和发送权限约束。",
          ws.tools
            .map((k) =>
              checked(
                "tools",
                k,
                toolNames[k] || "已登记工具",
                toolHelp[k] || "",
                c.tool_allowlist.includes(k),
              ),
            )
            .join(""),
        ) +
        field(
          "可以回答的内容",
          area("allowed", lines(c.allowed), "例如：已批准的产品功能和服务范围"),
          "每行一项，帮助助手把回答限制在产品范围内。",
        ) +
        field(
          "禁止承诺的内容",
          area("forbidden", lines(c.forbidden), "例如：保证交期、未批准的折扣"),
          "每行一项。留空表示未额外设置禁止用语。",
        );
    if (step === 3)
      body =
        wrap(
          "哪些情况转给人工",
          "触发后，系统冻结此对话的自动发送，并建立人工待办。",
          (ws.rules[a.workflow]?.triggers || [])
            .sort()
            .map((k) =>
              checked(
                "handoff",
                k,
                triggers[k]?.[0] || "其他已批准条件",
                triggers[k]?.[1] || "",
                c.handoff_on.includes(k),
              ),
            )
            .join(""),
        ) +
        '<h3 class="spaced">发布前检查</h3>' +
        checklist(checks(a, config())) +
        details(
          "高级配置",
          techRows({
            业务规则版本: ws.rules[a.workflow]?.ref,
            企业资料引用: ws.company_ref,
            产品资料引用: config().product_ref,
          }),
        );
    main().innerHTML =
      back("agents", "返回助手列表") +
      heading(
        id ? "编辑助手" : "创建助手",
        "按顺序配置角色，也可以随时保存草稿。",
      ) +
      '<div class="form-page">' +
      panel(
        titles[step],
        '<ol class="steps">' +
          titles
            .map(
              (s, i) =>
                '<li class="' +
                (i === step ? "on" : "") +
                '"><b>' +
                (i + 1) +
                "</b>" +
                s +
                "</li>",
            )
            .join("") +
          '</ol><form id="editor">' +
          body +
          '<div class="form-actions">' +
          (step ? btn("上一步", "previous", "返回上一组设置") : "") +
          btn("保存草稿", "save", "保存当前配置，稍后继续编辑") +
          (step < 3
            ? btn("下一步", "next", "继续填写" + titles[step + 1], "primary")
            : has("publish", "own_agents") &&
                (draft.model_editable !== false ||
                  c.model_provider !== "deepseek")
              ? btn("发布助手", "publish", "检查配置并发布新版本", "primary")
              : "") +
          link("取消", "agents", "btn", "返回助手列表，未保存的修改会丢失") +
          "</div></form>",
      ) +
      "</div>";
    if (step === 1)
      bindSelector(
        el("#editor"),
        c,
        () => {
          sync();
          dirty = true;
        },
        draw,
        async () => {
          connections = await connectionOptions();
        },
      );
    el("#editor").oninput = () => {
      dirty = true;
    };
    el("#editor").onsubmit = (e) => e.preventDefault();
    on(main(), "previous", () => {
      sync();
      step--;
      draw();
    });
    on(main(), "next", () => {
      sync();
      if (!step && (!a.name || !a.product_id))
        throw new Error("请先填写助手名称并选择产品。");
      step++;
      draw();
    });
    on(main(), "save", async () => {
      sync();
      await save();
      notify("草稿已保存，可以稍后继续编辑");
      go("agents");
    });
    on(main(), "publish", async () => {
      sync();
      const cfg = config();
      if (!c.goal || checks(a, cfg).some((i) => !i[0]))
        throw new Error("请按发布前检查补齐配置。");
      await formDialog({
        title: "发布助手",
        description:
          "先保存工作草稿，再由服务器校验并发布。新版本可用于新任务。",
        fields: checklist(checks(a, cfg)),
        submit: "确认发布",
        work: async () => {
          await save();
          const r = await post(pth(existing) + "/publish", {
            expected_revision: rev,
          });
          notify("助手已发布 · 第 " + r.version + " 版");
          go("agents/view/" + existing);
        },
      });
    });
  }
  async function save() {
    if (!a.name || !a.product_id)
      throw new Error("请先填写助手名称并选择产品。");
    if (!dirty && existing) return;
    const cfg = config();
    let r;
    if (existing) {
      r = await patch(pth(existing), {
        expected_revision: rev,
        changes: { name: a.name, config: cfg },
      });
    } else {
      r = await post("/agents", { ...a, config: cfg });
      existing = r.agent_id;
    }
    rev = r.draft_revision;
    dirty = false;
  }
  draw();
}
