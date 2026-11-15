import { field, control, area, esc } from "./ui.js";
export const triggers = {
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

const lines = (v) => (Array.isArray(v) ? v.join("\n") : "");
export const list = (v) =>
  v
    .split(/\n/)
    .map((x) => x.trim())
    .filter(Boolean);

export const checked = (name, id, label, help, yes) =>
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

export function splitGoal(goal = "") {
  const m = goal.match(/^主要工作：([^\n]*)\n工作目标：([\s\S]*)$/);
  return m ? { duty: m[1], goal: m[2] } : { duty: "", goal };
}

export function roleFields(c) {
  return (
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
      area("goal", c.goal, "例如：确认团队规模并安排演示", 'maxlength="11000"'),
      "描述一次对话希望得到的具体结果。",
    )
  );
}
export function scopeFields(c) {
  return (
    field(
      "可以回答的内容",
      area("allowed", lines(c.allowed), "例如：已批准的产品功能和服务范围"),
      "每行一项，帮助助手把回答限制在产品范围内。",
    ) +
    field(
      "禁止承诺的内容",
      area("forbidden", lines(c.forbidden), "例如：保证交期、未批准的折扣"),
      "每行一项。留空表示未额外设置禁止用语。",
    )
  );
}
export function handoffFields(values, options) {
  return [...options]
    .sort()
    .map((k) =>
      checked(
        "handoff",
        k,
        triggers[k]?.[0] || k,
        triggers[k]?.[1] || "",
        values.includes(k),
      ),
    )
    .join("");
}
export function joinedGoal(c) {
  return c.duty ? "主要工作：" + c.duty + "\n工作目标：" + c.goal : c.goal;
}
