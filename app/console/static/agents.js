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
import {
  roleFields,
  scopeFields,
  checked,
  handoffFields,
  splitGoal,
  joinedGoal,
  list,
  triggers,
} from "./role-fields.js";
import {
  libraryPage,
  chooseTemplate,
  templateSummary,
  exportLink,
} from "./templates.js";
import { practicePage, candidatePage } from "./practice.js";
import { researchPage, researchPolicy } from "./research.js";
const toolHelp = {
  "order.lookup": "只查询已经核实属于当前客户的模拟订单。",
  "channel.reply": "允许助手申请回复，发送前仍会检查渠道权限和人工归属。",
};
const pth = (id) => "/agents/" + encodeURIComponent(id);
const wrap = (name, hint, text) =>
  '<div class="field"><span>' +
  esc(name) +
  "</span>" +
  text +
  "<small>" +
  esc(hint) +
  "</small></div>";
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
  if (parts[1] === "research-policy") return researchPolicy(n);
  if (parts[1] === "research") return researchPage(n,parts.slice(2));
  if (parts[1] === "practice") return practicePage(n, parts.slice(2));
  if (parts[1] === "candidates") return candidatePage(n);
  if (parts[1] === "templates") return libraryPage(n, parts.slice(2));
  if (parts[1] === "new") return editor(n, null, parts[2], parts[3]);
  if (parts[1] === "edit") return editor(n, parts[2]);
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
        ((has("config") || has("publish")) ? link("研究来源与授权","agents/research-policy","btn","登记来源许可、审批助手的只读研究权限") : "") +
        (has("publish") ? link("启用审核", "agents/candidates", "btn", "审阅已提交的配置，不授予平台权限") : "") +
        (state.ws.manage_models
          ? link(
              "我的模型连接",
              "agents/connections",
              "btn",
              "添加或管理你自己使用的 DeepSeek 密钥",
            )
          : "") +
          (has("config", "own_agents")
            ? link(
                "从模板创建",
                "agents/templates/system",
                "primary",
                "选择模板，预填角色和工作目标",
              ) +
              link("创建助手", "agents/new", "btn", "从空白自定义创建助手") +
              link(
                "我的模板",
                "agents/templates/personal",
                "btn",
                "管理自己保存的配置",
              )
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
function checks(a, c, source = null) {
  const req = source?.requirements;
  return [
    [!!a.name.trim() && !!a.product_id, "已填写助手名称和所属产品"],
    [
      !!(c.persona?.trim() && c.tone?.trim() && c.goal?.trim()),
      "已填写角色、沟通风格和目标",
    ],
    [
      req?.knowledge === false || !!c.knowledge?.length,
      req?.knowledge === false
        ? "此场景允许不引用产品资料"
        : "已选择获批的资料版本",
    ],
    [!!c.allowed?.length, "已说明可以回答的内容"],
    [!!c.policy_ref, "已关联当前业务的接管规则"],
    [
      (!source || ["mock", "deepseek"].includes(c.model_provider)) &&
        (c.model_provider !== "deepseek" || !!c.model_connection_id),
      "已选择个人模型连接或模拟演示",
    ],
    ...(req?.handoff.length
      ? [
          [
            req.handoff.every((k) => c.handoff_on?.includes(k)),
            "已勾选必需的转人工条件",
          ],
        ]
      : []),
    ...(req?.tools.length
      ? [
          [
            req.tools.every(
              (k) =>
                state.ws.tools.includes(k) && c.tool_allowlist?.includes(k),
            ),
            "已选择已接入的必需工具",
          ],
        ]
      : []),
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
        (draft?.model_editable && view.workflow === "sales" ? link("企业研究","agents/research/"+id,"btn","申请只读工具、分析官网并审核联系草稿") : "") +
        (draft?.model_editable ? link("模拟客户对练", "agents/practice/" + id, "primary", "测试草稿或已发布版本，并查看历史") : "") +
        (draft?.model_editable
          ? exportLink(id, view.version, draft.draft_revision)
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
        使用的模型:
          view.source_template && !c.model_provider
            ? "待选择"
            : modelSummary(c),
        ...(view.source_template
          ? {
              创建来源:
                view.source_template.name +
                " · 第 " +
                view.source_template.version +
                " 版",
            }
          : {}),
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
        企业研究: c.research_tools?.length ? "已选择 · 实际权限按版本和来源审批" : "未选择",
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
  const tests = checks(draft, draft.config, draft.source_template);
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
    await formDialog({
      title: "发布前还需完善",
      fields: checklist(tests),
      submit: "去编辑",
      work: async () => go("agents/edit/" + id),
    });

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
async function editor(n, id, templateId, templateVersion) {
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
  let source = draft.source_template || null;
  if (templateId) {
    source = await request(
      "/console/manage/templates/" + encodeURIComponent(templateId),
    );
    if (templateVersion && Number(templateVersion) !== source.version)
      throw new Error("模板已更新，请返回模板库重新选择。");
    draft = {
      name: "",
      workflow: source.workflow,
      product_id: "",
      config: { ...source.config },
    };
  }
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
        if (wf && wf !== a.workflow) c.handoff_on = [];
        if (pid !== a.product_id) c.knowledge = [];
        a.workflow = wf || a.workflow;
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
      c.research_tools = f.has("research_wanted") && a.workflow==="sales" ? ["company.profile_read"] : [];
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
      goal: joinedGoal(c),
      allowed: c.allowed,
      forbidden: c.forbidden,
      knowledge: c.knowledge,
      tool_allowlist: c.tool_allowlist,
      ...(a.workflow==="sales" && c.research_tools?.length ? {research_tools:c.research_tools} : {}),
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
            (source && !locked()
              ? opt("", "请选择本次产品", a.product_id)
              : "") +
            ws.products.map((x) => opt(x.id, x.name, a.product_id)).join("") +
            "</select>",
          "助手只读取这款产品批准的资料。",
        ) +
        "</div>" +
        field(
          "业务类型",
          '<select name="workflow" ' +
            (locked() || source ? "disabled" : "") +
            ">" +
            opt("sales", "企业销售", a.workflow) +
            opt("support", "店铺客服", a.workflow) +
            "</select>",
          "销售负责商务沟通，客服处理来信。保存后产品和业务类型固定。",
        );
    if (step === 1)
      body =
        roleFields(c) +
        (draft.model_editable === false
          ? note("模型由助手创建者管理。你可以维护其他角色资料。")
          : (draft.model_needs_rebind
              ? note(
                  "此助手原先引用了其他账号的连接。请选择你自己的连接，或改用模拟模型。",
                  "warn",
                )
              : "") + selector(c, connections, Boolean(source)));
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
          (source ? ws.tools_by_product[a.product_id] || [] : ws.tools)
            .map((k) =>
              checked(
                "tools",
                k,
                toolNames[k] || "已登记工具",
                toolHelp[k] || "",
                c.tool_allowlist.includes(k),
              ),
            )
            .join("") || note("当前产品尚无已授权的工具，可以只生成草稿。"),
        ) +
        (a.workflow==="sales" ? wrap("企业官网资料研究 · 只读","勾选仅保存申请意愿。对练使用虚构资料；发布后还须按网站申请管理员授权。",checked("research_wanted","yes","企业官网资料研究","只读取许可页面，不会向企业发消息。",c.research_tools?.includes("company.profile_read"))) : "") +
        scopeFields(c);
    if (step === 3)
      body =
        wrap(
          "哪些情况转给人工",
          "触发后，系统冻结此对话的自动发送，并建立人工待办。",
          handoffFields(c.handoff_on, ws.rules[a.workflow]?.triggers || []),
        ) +
        '<h3 class="spaced">发布前检查</h3>' +
        checklist(checks(a, config(), source)) +
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
        existing
          ? ""
          : btn(
              source ? "更换模板" : "选择模板",
              "choose-template",
              "查看模板，确认后替换通用配置",
            ),
      ) +
      (source ? templateSummary(source) : "") +
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
          (draft.model_editable !== false ? btn("保存草稿并对练","practice-save","保存后选择模拟客户，不发布助手") : "") +
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
    on(main(), "choose-template", async () => {
      sync();
      const next = await chooseTemplate();
      if (!next || !active(n)) return;
      source = next;
      a.workflow = next.workflow;
      c = {
        ...next.config,
        ...splitGoal(next.config.goal),
        knowledge: [],
        tool_allowlist: [],
      };
      step = 0;
      dirty = true;
      draw();
    });
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
    on(main(), "practice-save", async () => { sync(); await save(); go("agents/practice/" + existing); });
    on(main(), "save", async () => {
      sync();
      await save();
      notify("草稿已保存，可以稍后继续编辑");
      go("agents");
    });
    on(main(), "publish", async () => {
      sync();
      const cfg = config();
      if (!c.goal || checks(a, cfg, source).some((i) => !i[0]))
        throw new Error("请按发布前检查补齐配置。");
      await formDialog({
        title: "发布助手",
        description:
          "先保存工作草稿，再由服务器校验并发布。新版本可用于新任务。",
        fields: checklist(checks(a, cfg, source)),
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
      r = await post("/agents", {
        ...a,
        config: cfg,
        ...(source
          ? { source_template: { id: source.id, version: source.version } }
          : {}),
      });
      existing = r.agent_id;
    }
    rev = r.draft_revision;
    dirty = false;
  }
  draw();
}
