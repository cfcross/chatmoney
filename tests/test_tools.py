from tests.test_workflows import prepare, plan
from tests.conftest import profile
from app.tools.gateway import Gateway
from app.workflows import drive
from app.channels import Sender
from app.db import transaction
from app import schema as s


def test_mcp_order_and_entity_ownership(env):
    _, sid = prepare(env, "toc")
    kw = dict(gateway=Gateway(), sender=Sender())
    out = drive(plan(env, "toc", sid, "我的订单 O7 发货了吗？", **kw), sid)
    assert (
        out["action"] == "reply"
        and out["delivery"] == "accepted"
        and "已发货" in out["text"]
    ), out
    out = drive(plan(env, "toc", sid, "查 O8 的物流", "e2", **kw), sid)
    assert (
        out["action"] == "suppressed"
        and out["reason"] == "ORDER_ACCESS_DENIED"
        and "备货" not in str(out)
    )
    with transaction("toc") as c:
        assert len(c.execute(s.receipts.select()).all()) == 1
        assert len(c.execute(s.tools.select()).all()) == 2


def test_tool_permission_even_for_forged_proposal(env):
    cfg = profile("toc")
    cfg["config"]["tool_allowlist"] = ["channel.reply"]
    _, sid = prepare(env, "toc", cfg=cfg)
    out = drive(
        plan(env, "toc", sid, "查订单O7", gateway=Gateway(), sender=Sender()), sid
    )
    assert out["reason"] == "TOOL_FORBIDDEN"
    with transaction("toc") as c:
        assert not c.execute(s.receipts.select()).first()
