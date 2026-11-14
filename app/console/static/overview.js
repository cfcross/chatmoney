import {
  state,
  has,
  directory,
  active,
  main,
  on,
  refresh,
  go,
  techRows,
} from "./app.js";
import {
  heading,
  esc,
  date,
  icon,
  btn,
  panel,
  empty,
  badge,
  kinds,
  link,
  navButton,
} from "./ui.js";
const shortcut = (ico, title, desc, path) =>
  '<a class="quick" href="#' +
  path +
  '"><span class="ico-tile">' +
  icon(ico) +
  '</span><span class="meta"><strong>' +
  title +
  "</strong><small>" +
  desc +
  '</small></span><span class="trail">' +
  icon("arrow", 16) +
  "</span></a>";
export async function overview(n) {
  const w = state.ws;
  const canViewRuns = has(
    "config",
    "publish",
    "operator",
    "observe",
    "business",
    "seat",
  );
  if (!canViewRuns && has("model_admin")) {
    main().innerHTML =
      heading("运行总览", w.tenant) +
      panel(
        "模型管理",
        shortcut(
          "bot",
          "我的模型连接",
          "添加、验证或更换自己的 DeepSeek 密钥",
          "agents/connections",
        ),
      );
    return;
  }
  const runs = await directory("runs", { limit: 6 });
  if (!active(n)) return;
  const counts = [
    ["bot", "助手数量", w.counts.agents, "agents"],
    ["pulse", "执行中任务", w.counts.running, "sessions/tasks/running"],
    ["clock", "等待中任务", w.counts.waiting, "sessions/tasks/waiting"],
    ["user", "人工待办会话", w.counts.handoffs, "handoffs"],
  ];
  const stats =
    '<div class="stats">' +
    counts
      .map(
        ([ico, label, v, path]) =>
          (v == null
            ? '<div class="stat">'
            : '<a class="stat" href="#' +
              path +
              '" title="查看' +
              label +
              '">') +
          "<small>" +
          icon(ico, 15) +
          label +
          "</small><b>" +
          esc(v ?? "暂未获取") +
          "</b>" +
          (v == null ? "</div>" : "</a>"),
      )
      .join("") +
    "</div>";
  const feed = runs.items.length
    ? runs.items
        .map(
          (r) =>
            '<a class="run-row" href="#sessions/run/' +
            encodeURIComponent(r.id) +
            '"><span class="ico-tile">' +
            icon(
              r.kind === "index"
                ? "file"
                : r.kind === "human_send"
                  ? "user"
                  : "chat",
            ) +
            '</span><span class="meta"><strong>' +
            esc(kinds[r.kind] || "业务处理") +
            "</strong><small>" +
            esc(r.name || r.agent || "工作区") +
            " · " +
            date(r.at) +
            "</small></span>" +
            badge("run", r.status) +
            "</a>",
        )
        .join("")
    : empty("还没有执行记录", "上传资料或开始客户会话后，这里会显示最近进展。");
  main().innerHTML =
    heading(
      "运行总览",
      "查看" + w.tenant + "的助手运行情况和需要人工处理的工作。",
      btn("刷新数据", "refresh", "重新读取当前工作区的任务、会话和执行记录"),
    ) +
    '<div class="statusbar"><span class="ico-tile">' +
    icon("pulse", 23) +
    "</span><div><strong>引擎运行正常</strong><p>已连接工作区 · " +
    esc(w.tenant) +
    '</p></div><div class="tools muted">最近更新 ' +
    date(w.at) +
    "</div></div>" +
    stats +
    '<div class="split"><div class="stack">' +
    panel("最近执行", feed) +
    '</div><div class="stack">' +
    panel(
      "常用操作",
      (has("config", "own_agents")
        ? shortcut("bot", "创建助手", "配置角色、资料和接管条件", "agents/new")
        : "") +
        (has("config", "publish", "business")
          ? shortcut(
              "book",
              w.manage_knowledge ? "管理产品资料" : "查看产品资料",
              w.manage_knowledge
                ? "管理资料库、文件与发布版本"
                : "阅读已发布的产品文件",
              "knowledge",
            )
          : "") +
        (has("seat", "observe", "business")
          ? shortcut(
              "user",
              "处理人工待办",
              "查看客户原话并认领会话",
              "handoffs",
            )
          : ""),
    ) +
    panel(
      "服务状态",
      techRows({
        服务连接: "运行正常",
        助手模型: "进入助手详情查看",
        DeepSeek用量: "仅使用助手创建者绑定的个人连接",
        消息渠道: "模拟渠道",
        后台处理进程: "暂未获取",
      }) +
        '<p class="help">引擎暂未提供后台进程的实时心跳。可通过最近执行核对任务进展。</p>',
    ) +
    "</div></div>";
  on(main(), "refresh", refresh);
}
