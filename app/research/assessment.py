"""The model selects evidence references; it cannot call tools, authorize contact, or invent facts."""

from datetime import datetime, UTC
import re
from app.research.network import Denied, within
from app.llm import ModelInput, ModelError
from app.llm.base import complete
from app.model_connections import model_call

LABELS = {
    "industry": "行业",
    "main_products": "主要产品",
    "business_description": "主营业务",
    "country_or_region": "地区",
    "service_market": "服务市场",
    "company_size": "企业规模",
    "demand_signal": "公开需求信号",
}


def collect(raw, src):
    evidence = []
    contacts = []
    seen = set()
    stamp = datetime.now(UTC).isoformat()
    for page in raw["pages"]:
        for x in page["facts"]:
            key = (x["field"], x["quote"])
            if key in seen or len(evidence) >= 30:
                continue
            seen.add(key)
            evidence.append(
                dict(
                    id="E" + str(len(evidence) + 1),
                    field=x["field"],
                    quote=x["quote"],
                    url=page["url"],
                    retrieved_at=stamp,
                )
            )
        if src.policy["allow_contact_page"]:
            contacts += [
                u for u in page["contact_candidates"] if within(u, src.base_url)
            ]
    if not evidence:
        raise Denied("EMPTY")
    return dict(
        evidence=evidence,
        contacts=list(dict.fromkeys(contacts))[:2],
        requests=raw["requests"],
        retrieved_at=stamp,
    )


def fit(evidence, rules):
    rows = []
    known = won = 0
    total = sum(r["weight"] for r in rules)
    for rule in rules:
        k = rule["field"]
        w = rule["weight"]
        allowed = {k}
        if k in ("industry", "business_description", "main_products"):
            allowed |= {"business_text", "business_description"}
        related = [e for e in evidence if e["field"] in allowed]

        def hits(terms, negated=False):
            ids = []
            for e in related:
                text = e["quote"].casefold()
                for term in terms:
                    word = term.casefold()
                    pattern = (
                        (r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
                        if word.isascii() and word.isalnum()
                        else re.escape(word)
                    )
                    for match in re.finditer(pattern, text):
                        before = text[max(0, match.start() - 36) : match.start()]
                        no = bool(
                            re.search(
                                r"(不(?:提供|含|销售|生产|经营|涉及|要求|包括|属于)|并非|不需要|(?:does not|do not|no longer|not|no)\s+(?:(?:sell|offer|provide|manufacture|a|an|the)\s+)?)\s*$",
                                before,
                            )
                        )
                        if no == negated and e["id"] not in ids:
                            ids.append(e["id"])
            return ids

        pos = hits(rule["include"])
        neg = list(
            dict.fromkeys(hits(rule.get("exclude", [])) + hits(rule["include"], True))
        )
        state = (
            "unknown" if (bool(pos) == bool(neg)) else ("match" if pos else "mismatch")
        )
        if state != "unknown":
            known += w
            if state == "match":
                won += w
        rows.append(
            dict(
                field=k,
                label=LABELS[k],
                weight=w,
                status=state,
                include=rule["include"],
                exclude=rule.get("exclude", []),
                refs=list(dict.fromkeys(pos + neg)),
                reason={
                    "match": "符合已配置的目标词",
                    "mismatch": "命中明确排除条件",
                    "unknown": "缺少证据或正反证据冲突",
                }[state],
            )
        )
    return dict(
        score=round(100 * won / known) if known and 2 * known >= total else None,
        coverage=round(100 * known / total),
        rules=rows,
    )


def compose(j, cfg, capture, refs, model=None):
    refs = [
        dict(
            id="P" + str(i + 1),
            document_id=x["document_id"],
            chunk_id=x["id"],
            quote=x["body"][:220],
        )
        for i, x in enumerate(refs)
    ]
    if not refs:
        raise Denied("SCOPE_CHANGED")
    es = capture["evidence"]
    choices = [
        e
        for e in es
        if e["field"]
        in {
            "main_products",
            "business_description",
            "business_text",
            "industry",
            "heading",
            "site_name",
            "page_title",
        }
    ] or es
    result = fit(es, j.input["criteria"])
    req = ModelInput(
        instructions="你是"
        + cfg["persona"]
        + "。目标："
        + cfg["goal"]
        + "。语气："
        + cfg["tone"]
        + "。允许回答："
        + "；".join(cfg.get("allowed", []))
        + "。禁止承诺："
        + "；".join(cfg.get("forbidden", []))
        + "。只根据给出的获批产品与网页证据，为人工联系草稿选择最相关的一条企业证据和一条产品资料。必须返回 JSON：evidence_id、product_id、approach。approach 仅能为了解需求、探讨场景或核实方向。只返回编号，不添加事实，不使用工具。",
        context={
            "research_selection": True,
            "company": [{"id": e["id"], "quote": e["quote"]} for e in choices],
            "product": [{"id": p["id"], "quote": p["quote"]} for p in refs],
        },
        current_message=j.input["research_goal"],
        mode="json",
        params={
            **(cfg.get("model_params") or {}),
            "max_tokens": min(
                240, (cfg.get("model_params") or {}).get("max_tokens", 240)
            ),
        },
    )
    out = complete(
        None,
        req,
        attempts=1,
        resolve=lambda: model_call(j.tenant, j.owner_id, cfg, override=model),
    )
    sol = out.structured_output or {}
    e = next((x for x in choices if x["id"] == sol.get("evidence_id")), None)
    p = next((x for x in refs if x["id"] == sol.get("product_id")), None)
    endings = {
        "了解需求": "如果这个方向与贵团队有关，方便介绍一下目前的需求吗？",
        "探讨场景": "如果方向合适，是否方便交流一下具体使用场景？",
        "核实方向": "想先核实这个方向是否与贵团队有关。",
    }
    if not e or not p or sol.get("approach") not in endings or out.tool_calls:
        raise ModelError("MODEL_INVALID_JSON")
    groups = {}
    for ev in es:
        groups.setdefault(ev["field"], []).append(
            {"value": ev["quote"], "ref": ev["id"]}
        )
    draft = (
        f"您好，在贵方公开页面上看到「{e['quote']}」。我们的产品资料提到「{p['quote']}」。"
        + endings[sol["approach"]]
    )
    return dict(
        company_name=groups.get("company_name", []),
        site_name=groups.get("site_name", []) or groups.get("page_title", []),
        official_website=j.target,
        fields=groups,
        fit=result,
        possible_cooperation=(
            "仅供核实：可探讨与已匹配条件有关的产品使用场景。"
            if result["score"] is not None and result["score"] > 0
            else "现有资料不能支持合作方向。"
        ),
        unknown_fields=[
            LABELS.get(k, k)
            for k in (
                "industry",
                "main_products",
                "country_or_region",
                "service_market",
                "company_size",
                "demand_signal",
            )
            if k not in groups
        ]
        + ["实际采购意向", "预算", "决策人"],
        intent_score=None,
        conversion_probability=None,
        contact_eligibility="unverified",
        public_business_contact=capture["contacts"],
        review_status="pending",
        evidence=es,
        requests=capture["requests"],
        retrieved_at=capture["retrieved_at"],
        draft=draft,
        draft_refs=[e["id"], p["id"]],
        product_evidence=[p],
        model=out.actual_model,
        usage=out.usage,
        contacted=False,
    )
