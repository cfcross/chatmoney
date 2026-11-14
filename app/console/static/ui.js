const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  bot: '<rect x="3" y="7" width="18" height="13" rx="4"/><path d="M12 3v4M7 12h2m6 0h2m-8 4h6"/>',
  book: '<path d="M3 5c3-1 6-1 9 1 3-2 6-2 9-1v14c-3-1-6-1-9 1-3-2-6-2-9-1zM12 6v14"/>',
  chat: '<path d="M21 12a7 7 0 0 1-7 7H8l-5 3v-6a7 7 0 0 1-1-4V9a6 6 0 0 1 6-6h7a6 6 0 0 1 6 6zM7 10h9m-9 4h6"/>',
  user: '<circle cx="9" cy="8" r="3.5"/><path d="M2 20v-2a6 6 0 0 1 12 0v2m2-10 5 0-2-2m2 2-2 2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pulse: '<path d="M2 12h5l3-7 4 14 3-7h5"/>',
  key: '<circle cx="8" cy="9" r="4"/><path d="m11 12 8 8 3-3m-6 0 2-2"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 13 3M5 14a7 7 0 0 0 13 3"/>',
  shield:
    '<path d="m12 2 8 3v7c0 5-5 8-8 10-3-2-8-5-8-10V5z"/><path d="m8 12 3 3 5-6"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h1m-1 6h1m-1 6h1"/>',
  file: '<path d="M5 2h9l5 5v15H5zM14 2v6h5M8 13h8m-8 4h6"/>',
  building:
    '<path d="M4 21h16M6 21V4h12v17M9 8h2m2 0h2m-6 4h2m2 0h2m-5 9v-5h4v5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/>',
  box: '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  back: '<path d="M19 12H5m5-5-5 5 5 5"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  play: '<path d="m8 4 12 8-12 8z"/>',
};
export const icon = (k, size = 18) =>
  '<svg aria-hidden="true" width="' +
  size +
  '" height="' +
  size +
  '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
  (paths[k] || paths.file) +
  "</svg>";
export const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
export const el = (q, root = document) => root.querySelector(q);
export const all = (q, root = document) => [...root.querySelectorAll(q)];

export const date = (v) =>
  v
    ? new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(v))
    : "暂未获取";
export const workflow = (w) =>
  w === "sales" ? "企业销售" : w === "support" ? "店铺客服" : "待确认";
export const states = {
  agent: { draft: "草稿", published: "已发布", disabled: "已停用" },
  task: {
    pending: "待启动",
    running: "运行中",
    waiting: "等待中",
    paused: "已暂停",
    completed: "已完成",
    cancelled: "已取消",
    failed: "执行失败",
  },
  session: {
    waiting: "等待中",
    running: "处理中",
    handoff_pending: "待人工认领",
    human_owned: "人工处理中",
    paused: "已暂停",
    ended: "已结束",
    error: "异常",
  },
  run: {
    queued: "已排队",
    running: "执行中",
    retry_wait: "等待重试",
    succeeded: "本轮已完成",
    failed: "执行失败",
    cancelled: "已取消",
  },
  delivery: {
    pending: "待发送",
    dispatching: "发送中",
    accepted: "平台已受理",
    failed: "发送失败",
    unknown: "发送结果待核实",
    not_sent: "未发送",
    cancelled: "已取消发送",
  },
  doc: {
    processing: "解析中",
    ready: "待批准",
    withdrawn: "已撤回",
    failed: "解析失败",
    published: "已批准",
    historical: "历史版本已批准",
  },
};
const green = ["published", "accepted", "succeeded", "completed"],
  amber = ["unknown", "human_owned", "handoff_pending", "retry_wait", "ready"],
  blue = ["running", "dispatching", "processing", "queued"];
export const badge = (group, k) =>
  '<span class="badge ' +
  (green.includes(k)
    ? "green"
    : amber.includes(k)
      ? "amber"
      : blue.includes(k)
        ? "blue"
        : ["failed", "error", "withdrawn"].includes(k)
          ? "red"
          : "neutral") +
  '"><i></i>' +
  esc(states[group]?.[k] || "状态待核实") +
  "</span>";
export const actions = {
  handoff: "转交人工",
  reply: "生成回复",
  draft: "保留草稿",
  suppressed: "已阻止自动操作",
  indexed: "资料已解析",
  started: "任务已启动",
  resumed: "已恢复处理",
  wait: "等待后续消息",
};
export const kinds = {
  index: "解析资料",
  started: "启动任务",
  resumed: "恢复任务",
  inbound: "处理客户来信",
  opening: "拟定首次邀请",
  follow_up: "跟进客户",
  release_graph: "恢复会话",
  human_send: "人工回复",
};
export const reasons = {
  formal_quote: "客户要求正式报价",
  contract: "涉及合同或付款",
  identity_question: "客户询问助手身份",
  ask_human: "客户要求真人",
  complaint: "客户提出投诉",
  refund: "涉及退款",
  invalid_analysis: "助手未能可靠理解来信",
  tool_limit: "已达工具调用上限",
  forbidden_claim: "涉及禁止承诺的内容",
  ORDER_ACCESS_DENIED: "无法核实订单属于当前客户",
  TOOL_FORBIDDEN: "助手没有工具权限",
  contact_denied: "当前不具备联系资格",
  state_blocked: "当前会话暂停自动处理",
  insufficient_evidence: "已批准的证据不足",
  owner_changed: "会话负责方已变化",
  frequency_limit: "已达到联系频率上限",
  profile_disabled: "助手已停用",
  opted_out: "客户已退订",
};
export const toolNames = {
  "order.lookup": "查询客户本人的订单",
  "channel.reply": "提交渠道回复",
};
export const roleNames = {
  config: "配置管理",
  publish: "资料与角色审核",
  seat: "人工坐席",
  business: "业务操作",
  operator: "任务管理",
  observe: "查看记录",
  adapter: "渠道接入",
  correct: "记忆纠正",
};
export const reason = (r) =>
  reasons[r] ||
  (!r
    ? "未记录原因"
    : /[\u3400-\u9fff]/.test(r)
      ? r
      : "请在执行详情中核对原因");
export function btn(text, action, note = "", cls = "btn", attrs = "") {
  return (
    '<button type="button" class="' +
    cls +
    '" data-act="' +
    action +
    '" title="' +
    esc(note || text) +
    '" ' +
    attrs +
    ">" +
    esc(text) +
    "</button>"
  );
}
export const link = (label, hash, cls = "link", note = label) =>
  '<a class="' +
  cls +
  '" href="#' +
  esc(hash) +
  '" title="' +
  esc(note) +
  '">' +
  esc(label) +
  "</a>";
export const panel = (title, body, side = "") =>
  '<section class="panel"><div class="panel-head"><h2>' +
  esc(title) +
  "</h2>" +
  side +
  "</div>" +
  body +
  "</section>";
export const heading = (title, sub, items = "") =>
  '<section class="page-head"><div><h1>' +
  esc(title) +
  "</h1><p>" +
  esc(sub) +
  '</p></div><div class="tools">' +
  items +
  "</div></section>";
export const empty = (title, detail, action = "") =>
  '<div class="empty">' +
  icon("layers", 28) +
  "<strong>" +
  esc(title) +
  "</strong><p>" +
  esc(detail) +
  "</p>" +
  action +
  "</div>";
export const note = (text, kind = "") =>
  '<p class="note ' +
  kind +
  '">' +
  icon("info", 16) +
  "<span>" +
  esc(text) +
  "</span></p>";
export const tag = (text) => '<span class="tag">' + esc(text) + "</span>";
export const field = (label, input, hint = "") =>
  '<label class="field"><span>' +
  esc(label) +
  "</span>" +
  input +
  (hint ? "<small>" + esc(hint) + "</small>" : "") +
  "</label>";
export const control = (name, value = "", placeholder = "", attrs = "") =>
  '<input name="' +
  name +
  '" value="' +
  esc(value) +
  '" placeholder="' +
  esc(placeholder) +
  '" ' +
  attrs +
  ">";
export const area = (name, value = "", placeholder = "", attrs = "") =>
  '<textarea name="' +
  name +
  '" rows="3" placeholder="' +
  esc(placeholder) +
  '" ' +
  attrs +
  ">" +
  esc(value) +
  "</textarea>";
export const opt = (value, label, active) =>
  '<option value="' +
  esc(value) +
  '" ' +
  (value === active ? "selected" : "") +
  ">" +
  esc(label) +
  "</option>";
export const details = (title, body) =>
  "<details><summary>" +
  esc(title) +
  '</summary><div class="detail-body">' +
  body +
  "</div></details>";
export const table = (head, rows) =>
  '<div class="table-scroll"><table><thead><tr>' +
  head.map((h) => "<th>" + esc(h) + "</th>").join("") +
  "</tr></thead><tbody>" +
  rows
    .map(
      (cols) =>
        "<tr>" + cols.map((c) => "<td>" + c + "</td>").join("") + "</tr>",
    )
    .join("") +
  "</tbody></table></div>";
export const navPages = {
  overview: ["运行总览", "grid"],
  members: ["成员账号", "user"],
  agents: ["助手管理", "bot"],
  knowledge: ["企业与产品资料库", "book"],
  sessions: ["客户会话与任务", "chat"],
  handoffs: ["人工接管", "user"],
};
export const navButton = (text, hash, ico) =>
  '<a class="btn" href="#' + esc(hash) + '">' + icon(ico) + esc(text) + "</a>";
export const idAttr = (id) => 'data-id="' + esc(id) + '"';
export function pager(data) {
  return (
    '<div class="pager"><span>共 ' +
    data.total +
    " 条" +
    (data.total ? " · 第 " + data.page + " 页" : "") +
    "</span><div>" +
    btn(
      "上一页",
      "prev",
      "查看上一页",
      "btn small",
      data.page <= 1 ? "disabled" : "",
    ) +
    btn(
      "下一页",
      "next",
      "查看更多记录",
      "btn small",
      data.page * data.limit >= data.total ? "disabled" : "",
    ) +
    "</div></div>"
  );
}
export const deliveryHint =
  "平台已受理表示模拟渠道接受了请求，客户是否收到尚无送达凭证。发送结果待核实须先查回执。未发送表示没有外发。";
