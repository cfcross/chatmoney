import {
  state,
  request,
  post,
  patch,
  main,
  active,
  on,
  notify,
  formDialog,
  confirmAction,
  go,
  back,
  techRows,
  permission,
  has,
} from "./app.js";
import {
  el,
  esc,
  opt,
  heading,
  link,
  btn,
  field,
  control,
  area,
  note,
  panel,
  empty,
  details,
  workflow,
} from "./ui.js";
import {
  roleFields,
  scopeFields,
  handoffFields,
  checked,
  splitGoal,
  joinedGoal,
  list,
  triggers,
} from "./role-fields.js";

const base = "/console/manage/templates";
const route = "agents/templates/";
const url = (id) => base + "/" + encodeURIComponent(id);
const labels = (values) => values.map((k) => triggers[k]?.[0] || k).join("、");
const formLink = (text, path) => link(text, route + path, "btn");
const applyLink = (t) =>
  link(
    "使用此模板",
    "agents/new/" + t.id + "/" + t.version,
    "primary",
    "在创建向导中补充产品资料和个人模型",
  );
const info =
  "模板只提供通用配置。请选择这次使用的产品资料、个人模型和实际可用的工具。";
const copyable = () => has("config", "own_agents");

export const exportLink = (id, published, draft) =>
  formLink(
    "保存为我的模板",
    "export/" +
      id +
      "/" +
      (published ? "published/" + published : "draft/" + draft),
  );

function toolAdvice(t) {
  return t.recommended_tools
    .map((k) =>
      k==="company.profile_read" ? "企业官网资料研究（已接入，只读，须另行申请）" : state.ws.tools.includes(k)
        ? k + "（已接入模拟工具，须另选）"
        : k + "（尚未接入）",
    )
    .join("、");
}

export function templateSummary(t) {
  return (
    '<div class="template-origin"><strong>已套用：' +
    esc(t.name) +
    "</strong><span>" +
    esc(info) +
    "</span>" +
    (t.recommended_tools?.length
      ? "<small>推荐工具：" + esc(toolAdvice(t)) + "</small>"
      : "") +
    "</div>"
  );
}

export async function chooseTemplate() {
  const [system, mine] = await Promise.all(
    ["system", "personal"].map((kind) => request(base, { query: { kind } })),
  );
  const group = (name, rows) =>
    '<optgroup label="' +
    name +
    '">' +
    rows.map((x) => opt(x.id, x.name, "")).join("") +
    "</optgroup>";
  let selected = null;
  await formDialog({
    title: "更换创建模板",
    description:
      "确认后会替换角色、目标、风格、范围和转人工设置，并清空模型、资料和工具选择。助手名称和产品会保留。",
    fields: field(
      "选择模板",
      '<select name="template" required>' +
        opt("", "请选择", "") +
        group("常用模板", system) +
        group("我的模板", mine) +
        "</select>",
    ),
    submit: "确认套用",
    work: async (f) => {
      selected = await request(url(f.get("template")));
    },
  });
  return selected;
}

function detailRows(t) {
  return techRows({
    适用场景: t.scenario,
    适用平台: t.platforms.join("、") || "自行选择",
    适用客户: t.audience || "自行确定",
    角色: t.config.persona,
    工作目标: t.config.goal,
    沟通风格: t.config.tone,
    工作范围: t.config.allowed.join("；"),
    禁止行为: t.config.forbidden.join("；"),
    规则转人工: labels(t.config.handoff_on) || "未设置",
    人工核对建议: t.human_review || "自行补充",
    需要的资料:
      t.materials ||
      (t.requirements.knowledge ? "本产品已批准的资料" : "按需选择"),
    推荐工具: t.recommended_tools.length ? toolAdvice(t) : "无",
    发布必需工具: t.requirements.tools.join("、") || "无",
    必须保留的交接条件: labels(t.requirements.handoff) || "按业务选择",
  });
}

async function library(n, kind) {
  const rows = await request(base, { query: { kind } });
  if (!active(n)) return;
  const personal = kind === "personal";
  const cards = rows
    .map(
      (t) =>
        '<article class="template-card" data-template="' +
        esc(t.id) +
        '"><h3>' +
        link(t.name, route + "view/" + t.id) +
        "</h3><p>" +
        esc(t.description) +
        "</p><dl><dt>适用业务</dt><dd>" +
        esc(t.scenario) +
        "</dd><dt>适用平台</dt><dd>" +
        esc(t.platforms.join("、") || "按本次任务选择") +
        '</dd></dl><div class="actions">' +
        (copyable() ? applyLink(t) : "") +
        formLink("查看详情", "view/" + t.id) +
        (personal
          ? details(
              "更多操作",
              formLink("编辑", "edit/" + t.id) +
                btn("复制", "copy", "创建个人副本") +
                btn("删除", "delete", "删除本人模板", "danger"),
            )
          : "") +
        "</div></article>",
    )
    .join("");
  main().innerHTML =
    back("agents", "返回助手管理") +
    heading(
      personal ? "我的模板" : "常用模板",
      personal
        ? "只有你可以查看和管理这里的模板。"
        : "按业务场景选择。套用后可以修改，再保存为自己的助手。",
      formLink(
        personal ? "常用模板" : "我的模板",
        personal ? "system" : "personal",
      ) +
        (personal && copyable()
          ? link("新建模板", route + "new", "primary")
          : ""),
    ) +
    (cards
      ? '<div class="template-grid">' + cards + "</div>"
      : empty(
          "还没有个人模板",
          "可以新建模板，也可以从自己的助手详情保存一份。",
        ));
  if (personal)
    bindActions((button) =>
      rows.find(
        (t) => t.id === button.closest("[data-template]").dataset.template,
      ),
    );
}

async function detail(n, id) {
  const t = await request(url(id));
  if (!active(n)) return;
  const personal = t.type === "personal";
  main().innerHTML =
    back(route + (personal ? "personal" : "system"), "返回模板库") +
    heading(
      t.name,
      t.description,
      (copyable()
        ? applyLink(t) + btn("复制为我的模板", "copy", "新建自己的副本")
        : "") +
        (personal
          ? formLink("编辑模板", "edit/" + id) +
            btn("删除模板", "delete", "删除本人的模板", "danger")
          : ""),
    ) +
    panel("配置详情", detailRows(t) + note(info));
  bindActions(() => t);
}
function bindActions(resolve) {
  on(main(), "copy", async (button) => {
    const t = resolve(button),
      id = t.id;
    return formDialog({
      title: "复制为我的模板",
      fields: field(
        "副本名称",
        control(
          "name",
          t.name.slice(0, 194) + "（副本）",
          "",
          "required maxlength=200",
        ),
      ),
      submit: "保存副本",
      work: async (f) => {
        const r = await post(url(id) + "/copy", {
          name: f.get("name"),
          expected_version: t.version,
        });
        notify("副本已保存");
        go(route + "view/" + r.id);
      },
    });
  });
  on(main(), "delete", async (button) => {
    const t = resolve(button),
      id = t.id;
    return confirmAction(
      "删除模板",
      "删除后无法再从这里套用。以前创建的助手及其任务继续使用各自保存的配置。",
      async () => {
        await request(url(id), {
          method: "DELETE",
          query: { expected_version: t.version },
        });
        notify("模板已删除");
        go(route + "personal");
      },
    );
  });
}

function editorFields(t) {
  const scoped = t.config || {};
  const simple = (label, k, required = false) =>
    field(
      label,
      control(
        k,
        t[k] || "",
        "",
        (required ? "required " : "") + "maxlength=200",
      ),
    );
  return (
    panel(
      "模板介绍",
      simple("模板名称", "name", true) +
        simple("一句话介绍", "description", true) +
        field(
          "业务类型",
          '<select name="workflow">' +
            opt("sales", "企业销售", t.workflow) +
            opt("support", "店铺客服", t.workflow) +
            "</select>",
        ) +
        simple("适用场景", "scenario", true) +
        field(
          "适用平台",
          area("platforms", t.platforms.join("\n")),
          "每行一个，仅作场景说明。",
        ),
    ) +
    panel(
      "可复用的角色设置",
      roleFields({ ...scoped, ...splitGoal(scoped.goal) }) +
        scopeFields(scoped),
    ) +
    panel(
      "交给人工",
      '<div id="template-signals"></div>' +
        field(
          "其他人工核对建议",
          area("human_review", t.human_review, "", "maxlength=600"),
          "例如技术参数冲突时交给工程师。这类文字供人核对，系统只自动执行已登记的条件。",
        ),
    ) +
    details(
      "适用客户、资料和发布要求",
      field("目标客户", area("audience", t.audience, "", "maxlength=400")) +
        field(
          "需要准备的资料",
          area("materials", t.materials, "", "maxlength=400"),
        ) +
        checked(
          "need_knowledge",
          "yes",
          "发布前必须选产品资料",
          "引用产品事实时勾选。",
          t.requirements.knowledge,
        ) +
        field(
          "建议研究的工具",
          area("recommended_tools", t.recommended_tools.join("\n")),
          "每行一项，仅作建议。",
        ) +
        field(
          "发布必需的工具",
          area("required_tools", t.requirements.tools.join("\n")),
          "通常留空。填写的工具必须已接入，并由用户另行选择和获准使用。",
        ),
    )
  );
}

async function editPage(n, kind, id, view, rawVersion) {
  if (!copyable()) {
    main().innerHTML = permission();
    return;
  }
  let t = {
    name: "",
    description: "",
    scenario: "",
    workflow: "sales",
    config: {},
    platforms: [],
    audience: "",
    materials: "",
    human_review: "",
    recommended_tools: [],
    requirements: { knowledge: true, tools: [], handoff: [] },
  };
  let source,
    choices = "";
  if (kind === "edit") {
    t = await request(url(id));
    if (t.type !== "personal") throw new Error("系统模板只读，请先复制。");
  } else if (kind === "export") {
    source = { view, version: Number(rawVersion) };
    const prefix = "/console/manage/agents/" + encodeURIComponent(id);
    const preview = await request(prefix + "/template-preview", {
      query: source,
    });
    const draft = await request("/agents/" + encodeURIComponent(id), {
      query: { view: "draft" },
    });
    const rec = (
      await request("/console/data/directory/agents", { query: { id } })
    ).items[0];
    t = { ...t, ...preview, name: draft.name.slice(0, 195) + "模板" };
    const versions = [["draft/" + draft.draft_revision, "当前工作草稿"]];
    for (let i = rec.version; i >= 1; i--)
      versions.push(["published/" + i, "已发布 · 第 " + i + " 版"]);
    choices = field(
      "复制哪一版",
      '<select name="source_version">' +
        versions.map(([k, v]) => opt(k, v, view + "/" + rawVersion)).join("") +
        "</select>",
    );
  }
  if (!active(n)) return;
  main().innerHTML =
    back(kind === "export" ? "agents/view/" + id : route + "personal", "返回") +
    heading(
      kind === "export"
        ? "保存为我的模板"
        : kind === "edit"
          ? "编辑模板"
          : "新建模板",
      "模板只对本人可见。保存后可反复用于创建助手。",
    ) +
    '<form class="form-page template-form" id="template-form">' +
    choices +
    (source
      ? note(
          "保留角色、目标、风格、范围和交接设置。模型连接、资料绑定、工具授权、客户与任务均不复制。请把角色文字中的专有信息替换为【产品】等占位词。",
        )
      : "") +
    editorFields(t) +
    '<div class="form-actions">' +
    btn("保存模板", "save-template", "保存到我的模板", "primary") +
    formLink("取消", "personal") +
    "</div></form>";
  const form = el("#template-form");
  const wf = form.elements.workflow;
  if (source) wf.disabled = true;
  function drawSignals(selected, required) {
    const keys = state.ws.rules[wf.value]?.triggers || [];
    el("#template-signals").innerHTML =
      handoffFields(selected, keys) +
      details(
        "发布时必须保留的条件",
        keys
          .map((k) =>
            checked(
              "required_signal",
              k,
              triggers[k]?.[0] || k,
              "发布时会检查是否已勾选。",
              required.includes(k),
            ),
          )
          .join(""),
      );
  }
  drawSignals(t.config.handoff_on || [], t.requirements.handoff);
  wf.onchange = () => drawSignals([], []);
  if (source)
    form.elements.source_version.onchange = async (ev) => {
      const choice = ev.target.value;
      const changed = await formDialog({
        title: "更换来源版本",
        description: "将重新载入所选版本的通用设置。当前未保存的编辑会被替换。",
        submit: "载入此版本",
        work: async () => go(route + "export/" + id + "/" + choice),
      });
      if (!changed) ev.target.value = view + "/" + rawVersion;
    };
  form.onsubmit = (e) => e.preventDefault();
  on(form, "save-template", async () => {
    if (!form.reportValidity()) return;
    const f = new FormData(form);
    const value = (k) => String(f.get(k) || "").trim();
    const lines = (k) => list(value(k));
    const config = {
      persona: value("persona"),
      tone: value("tone"),
      goal: joinedGoal({ duty: value("duty"), goal: value("goal") }),
      allowed: lines("allowed"),
      forbidden: lines("forbidden"),
      handoff_on: f.getAll("handoff"),
    };
    if (!config.persona || !value("goal") || !config.tone)
      throw new Error("请填写角色、工作目标和沟通风格。");
    const requirement = f.getAll("required_signal");
    if (requirement.some((k) => !config.handoff_on.includes(k)))
      throw new Error("请先启用被列为必需的转人工条件。");
    const body = {
      name: value("name"),
      description: value("description"),
      workflow: wf.value,
      scenario: value("scenario"),
      platforms: lines("platforms"),
      audience: value("audience"),
      materials: value("materials"),
      recommended_tools: lines("recommended_tools"),
      human_review: value("human_review"),
      config,
      requirements: {
        knowledge: f.has("need_knowledge"),
        tools: lines("required_tools"),
        handoff: requirement,
      },
    };
    const result = source
      ? await post(
          "/console/manage/agents/" + encodeURIComponent(id) + "/templates",
          { ...source, content: body },
        )
      : kind === "edit"
        ? await patch(url(id), { expected_version: t.version, content: body })
        : await post(base, body);
    notify("个人模板已保存");
    go(route + "view/" + result.id);
  });
}

export async function libraryPage(n, [kind = "system", id, view, version]) {
  if (["system", "personal"].includes(kind)) return library(n, kind);
  if (kind === "view") return detail(n, id);
  if (["new", "edit", "export"].includes(kind))
    return editPage(n, kind, id, view, version);
  go(route + "system");
}
