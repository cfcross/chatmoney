"""Demo policies. The shared runtime consumes decisions; the business owns these rules."""

from typing import Literal
from pydantic import BaseModel, Field


class Decision(BaseModel):
    decision: Literal["allow", "draft_only", "handoff", "deny"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    next_due_at: str | None = None


class Analysis(BaseModel):
    facts: dict[str, str | int | float | None]
    need: str
    signals: list[str]


SALES = {
    "formal_quote": ("正式报价", "盖章报价"),
    "contract": ("合同", "付款"),
    "ask_human": ("真人", "转人工"),
    "identity_question": ("你是不是机器人", "你是机器人", "你是真人吗"),
}
SUPPORT = {
    "refund": ("退款", "退钱"),
    "complaint": ("投诉",),
    "ask_human": ("真人", "人工客服"),
    "identity_question": ("你是不是机器人",),
}
REGISTRY = {
    "sales.policy@1": ("sales", SALES),
    "support.policy@1": ("support", SUPPORT),
}


def evaluate(policy_ref, phase, facts, action, evidence_refs):
    entry = REGISTRY.get(policy_ref)

    def result(d, r):
        return Decision(decision=d, reason=r, evidence_refs=evidence_refs)

    if not entry:
        return result("deny", "unknown_policy")
    text = facts.get("message", "")
    triggers = set(facts.get("handoff_on", []))
    for signal, terms in entry[1].items():
        if signal in triggers and (
            signal in facts.get("signals", []) or any(term in text for term in terms)
        ):
            return result("handoff", signal)
    if phase == "output":
        if any(term in action for term in facts.get("forbidden", []) if term):
            return result("handoff", "forbidden_claim")
        if not facts.get("grounded"):
            return result("draft_only", "insufficient_evidence")
    return result("allow", "policy_allowed")
