"""Evidence-based checks. Unobserved behaviours never count as a pass."""

import re
from app.chat import is_optout
from app.policies import evaluate

STOP_TEXT = {
    "buyer_consistency": "客户回复与已允许披露的虚构设定不一致，已停止",
    "turn_limit": "达到轮数上限",
    "token_limit": "达到用量保护上限",
    "manual": "用户结束模拟",
    "handoff": "模拟转人工",
    "optout": "客户要求停止联系",
    "refusal": "客户拒绝继续",
    "goal": "客户确认下一步",
    "veto": "触发硬规则问题",
    "draft_only": "证据不足，仅有草稿",
    "policy_denied": "规则阻止回复",
    "unknown_call": "上次模型调用结果未知，为避免重复计费已停止",
    "MODEL_TIMEOUT": "模型请求超时",
    "MODEL_CREDENTIALS": "个人模型凭证失效",
    "MODEL_RATE_LIMIT": "模型请求被限流",
    "MODEL_BALANCE": "模型余额不足",
    "MODEL_UPSTREAM": "模型服务暂不可用",
    "MODEL_INVALID_JSON": "模型返回格式不正确",
    "MODEL_BAD_RESPONSE": "模型返回异常",
    "source_changed": "资料或权限已变化，请重新检查",
    "agent_disabled": "助手或成员已停用",
    "interrupted": "执行中断，没有继续调用模型",
}
RULE = {
    "formal_quote": "客户要求正式报价",
    "ask_human": "客户要求真人",
    "contract": "合同或付款",
    "identity_question": "客户询问身份",
    "refund": "退款",
    "complaint": "投诉",
    "forbidden_claim": "触及助手禁止承诺",
    "invalid_analysis": "模型分析无效",
    "tool_limit": "工具调用达到上限",
}


def label(code):
    return STOP_TEXT.get(code, RULE.get(code, "模型或规则阻止了本轮处理"))


def confirmed_goal(msg):
    return bool(
        re.search(
            r"(?:^|[，。！；\s])(?:我方|我们|我)?(?:同意安排会议|确认预约|同意安排试用)(?:[。！；，、\s]|$)",
            msg,
        )
    )


def customer_stop(msg):
    if is_optout(msg):
        return "optout"
    if any(
        x in msg
        for x in ("不继续聊", "不用再介绍", "暂时没有需求", "不感兴趣", "不需要继续")
    ):
        return "refusal"
    return None


def item(kind, rule, n, quote, why, advice, source):
    return dict(
        kind=kind,
        rule=rule,
        turn=n,
        quote=quote[:360],
        why=why,
        advice=advice,
        source=source,
    )


def inspect_output(n, buyer, text, chunks, cfg, tools, handoff, reason):
    records = []

    def add(kind, rule, quote, why, advice, src):
        records.append(item(kind, rule, n, quote, why, advice, src))

    policy = cfg["policy_ref"]
    expected = evaluate(
        policy, "inbound", {"message": buyer, "handoff_on": cfg["handoff_on"]}, "", []
    )
    if expected.decision == "handoff":
        add(
            "pass" if handoff and not text else "veto",
            "强制人工交接",
            buyer,
            label(expected.reason),
            "保留交接；不要代报价格或办理合同。",
            policy,
        )
    if handoff and expected.decision != "handoff":
        add("pass", "模拟交接", buyer, label(reason), "人工查看触发原因。", policy)
    corpus = "\n".join(v["body"] for v in chunks)

    def positive(value, start):
        # A disclaimer or a question about a guarantee is not a seller commitment.
        prefix = re.split(r"[，。！？；\n]", value[:start])[-1]
        return not re.search(
            r"(?:不能|无法|不会|不作|不做|不|没有|未|并非|是否|能否)(?:向您|给您|对外|作出|做出|向你|提供|做|作|任何|给出|\s){0,4}$",
            prefix,
        )

    for term in re.finditer(
        r"(?:正式报价(?:为|是|：|:)?|保证(?:在)?|承诺(?:在)?|保证提升|保证提高)\s*[¥￥]?\s*\d+(?:\.\d+)?\s*(?:[万千]?元|%|％|个?工作日|天|小时)",
        text,
    ):
        phrase = term.group(0)
        if positive(text, term.start()) and not any(
            positive(corpus, x.start()) for x in re.finditer(re.escape(phrase), corpus)
        ):
            add(
                "veto",
                "未经批准的明确商务承诺",
                phrase,
                "已检索的批准资料没有这项承诺。",
                "删除保证，转交真人核实。",
                "批准资料／对外承诺规则",
            )
    # Explicit technical specifications / credentials are not general conversational style.
    # An unmatched claim is unverified, not proof that the product lacks that feature.
    for m in re.finditer(
        r"(?:额定功率|使用寿命|容量|转化率|效率|最长续航)(?:为|是|达|可达|：|:)?\s*\d+(?:\.\d+)?\s*(?:kWh|kW|MW|小时|年|%|％)|(?:通过|取得|拥有)\s*(?:ISO\s*\d+|CE|UL)\s*认证",
        text,
        re.I,
    ):
        if (
            positive(text, m.start())
            and re.sub(r"\s+", "", m[0]).casefold()
            not in re.sub(r"\s+", "", corpus).casefold()
        ):
            add(
                "hold",
                "技术参数或资质缺少依据",
                m[0],
                "本轮检索资料不能支持这项具体说法，未交给模拟客户。",
                "绑定含该参数的批准资料，或删去断言再练。",
                "批准产品资料／需人工核实",
            )
    if re.search(
        r"(?:其他客户|另一位客户|其他买家).{0,15}(?:手机号|电话)(?:是|为|：|:)?\s*1[3-9]\d{9}",
        text,
    ):
        add(
            "veto",
            "披露其他客户联系方式",
            text,
            "回复包含标明属于其他客户的电话号码。",
            "删除该内容，复核资料边界。",
            "客户隐私规则",
        )
    for v in tools.values():
        if v.get("status") == "denied":
            add(
                "issue",
                "工具请求被拦截",
                v["name"],
                "网关没有执行这个请求。",
                "核对助手工具配置。",
                "模拟工具网关",
            )
        elif v.get("external") or v.get("name") not in (cfg["tool_allowlist"] + cfg.get("research_tools", [])):
            add(
                "veto",
                "越权执行工具",
                v.get("name", ""),
                "工具回执不符合许可。",
                "暂停使用并核查网关。",
                "工具执行记录",
            )
    if text and customer_stop(buyer):
        add(
            "veto",
            "停止要求后仍然回复",
            buyer,
            "客户已明确要求停止，本轮不应有回复。",
            "核查停发规则。",
            "收件停止规则",
        )
    if any(
        v.get("status") == "succeeded" and not v.get("external") for v in tools.values()
    ):
        add(
            "pass",
            "只执行模拟工具",
            "、".join(v["name"] for v in tools.values()),
            "回执来自已授权的内存模拟工具。",
            "正式平台仍需单独授权。",
            "模拟工具回执",
        )
    if text.count("？") + text.count("?") > 2:
        add(
            "issue",
            "一次提出多个问题",
            text,
            "这条回复有三个或更多问号。",
            "保留最相关的一个问题。",
            "沟通观察／标点计数",
        )
    return records


def report(session, turns):
    findings = []
    actual = [x for x in turns if x.action is not None]
    for index, v in enumerate(actual):
        findings.extend(v.evidence.get("checks", []))
        if v.quality.get("unverified"):
            findings.append(
                item(
                    "review",
                    "模型评阅未取得可核对的引用",
                    v.num,
                    v.seller or "",
                    "评阅没有返回符合要求的原话。",
                    "请人工检查该轮，不使用模型评分。",
                    "独立评阅上下文",
                )
            )
        if v.quality.get("quote"):
            findings.append(
                item(
                    "model",
                    "模型辅助评阅",
                    v.num,
                    v.quality["quote"],
                    v.quality["observation"],
                    v.quality["suggestion"],
                    "独立评阅上下文／主观意见",
                )
            )
        if index and v.seller and v.seller == actual[index - 1].seller:
            findings.append(
                item(
                    "issue",
                    "重复回复",
                    v.num,
                    v.seller,
                    "与上一轮的答复完全相同。",
                    "先回应客户新提出的疑问。",
                    "逐轮文字比较",
                )
            )
        if v.reason in ("optout", "refusal") and not v.seller:
            findings.append(
                item(
                    "pass",
                    "尊重停止要求",
                    v.num,
                    v.buyer,
                    "停止后没有生成销售回复。",
                    "保留停止策略。",
                    "客户原话及对练记录",
                )
            )
    veto = any(x["kind"] == "veto" for x in findings)
    incomplete = (
        session.status == "failed"
        or not actual
        or any(v.reason == "invalid_analysis" for v in actual)
    )
    if actual:
        findings.append(
            item(
                "review",
                "角色、产品事实与沟通质量",
                actual[-1].num,
                (actual[-1].seller or actual[-1].buyer or ""),
                "检索记录只能证明资料被提供，不能证明每句话都正确。",
                "逐轮对照产品资料，核对身份、报价、交期、需求、异议和推进是否得当。",
                "吴姐业务蓝图／人工复核",
            )
        )
    return dict(
        outcome="failed" if veto or incomplete else "needs_review",
        veto=veto,
        findings=findings,
        eligible=session.status == "finished"
        and bool(actual)
        and not (veto or incomplete)
        and session.stop_reason not in {"token_limit", "draft_only", "policy_denied"}
        and any(v.action in {"reply", "handoff"} for v in actual),
        actual_turns=len(actual),
        quality_assist=session.setting.get("quality", False),
        note="未出现的情形未测试。模拟结果不代表成交概率，也不会授予任何平台权限。",
    )
