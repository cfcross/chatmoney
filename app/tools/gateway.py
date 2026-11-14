import asyncio
from datetime import timedelta
from typing import Annotated
import anyio
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from sqlalchemy import select
from app import schema as s
from app.db import insert, row, uid
from app.workflows import allowed, entities
from app.catalog import version


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderArgs(Strict):
    order_id: str = Field(min_length=1, max_length=80)


class ReplyArgs(Strict):
    text: str = Field(min_length=1, max_length=12000)


class OrderResult(Strict):
    order_id: str
    shipping: str


class ReplyResult(Strict):
    intent: str


REGISTRY = {
    "order.lookup": (OrderArgs, OrderResult, "查询当前客户的模拟订单"),
    "channel.reply": (ReplyArgs, ReplyResult, "提交拟发送的文字，需统一发送检查"),
}


def server_for(order):
    # A fresh trusted capability bound to the authorized row. Model arguments cannot change its scope.
    server = FastMCP("ChatMoney local mock")

    @server.tool(name="order.lookup")
    def lookup(
        order_id: Annotated[str, Field(min_length=1, max_length=80)],
    ) -> OrderResult:
        if not order or order["order_id"] != order_id:
            raise ValueError("ORDER_ACCESS_DENIED")
        return OrderResult(**order)

    @server.tool(name="channel.reply")
    def reply(
        text: Annotated[str, Field(min_length=1, max_length=12000)],
    ) -> ReplyResult:
        return ReplyResult(intent="proposed")

    return server


async def exchange(tool, args, order):
    with anyio.fail_after(5):
        async with create_connected_server_and_client_session(
            server_for(order), read_timeout_seconds=timedelta(seconds=3)
        ) as client:
            catalog = await client.list_tools()
            found = next((x for x in catalog.tools if x.name == tool), None)
            expected = REGISTRY[tool][0].model_json_schema()
            if (
                not found
                or found.inputSchema.get("properties") != expected["properties"]
                or found.inputSchema.get("required") != expected["required"]
            ):
                raise ValueError("TOOL_SCHEMA_CHANGED")
            resp = await client.call_tool(tool, args)
            if resp.isError:
                raise ValueError("TOOL_ERROR")
            return REGISTRY[tool][1].model_validate(resp.structuredContent).model_dump()


class Gateway:
    def descriptions(self, c, t, cfg, cv):
        account = row(c, s.accounts, t, cv.account_id)
        if not account.enabled:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": name.replace(".", "__"),
                    "description": desc,
                    "parameters": args.model_json_schema(),
                },
            }
            for name, (args, _, desc) in REGISTRY.items()
            if name in cfg["tool_allowlist"] and name in account.capabilities
        ]

    def execute(self, ex, tc, key):
        t = ex.tenant
        with ex.tx() as (c, r):
            cached = (
                c.execute(
                    select(s.tools).where(
                        s.tools.c.tenant == t,
                        s.tools.c.run_id == r.id,
                        s.tools.c.action_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if cached:
                return cached.outcome
            se, task, a, cv = entities(c, t, r)
            cfg = version(c, t, a.id, task.agent_version).config
            name = tc["name"]
            account = row(c, s.accounts, t, cv.account_id)
            eid = uid("tool")
            result = dict(execution_id=eid, status="denied", data=None, error=None)
            order = None
            if (
                not allowed(se, task, a, cv, r)
                or not account.enabled
                or name not in REGISTRY
                or name not in cfg["tool_allowlist"]
                or name not in account.capabilities
            ):
                result["error"] = "TOOL_FORBIDDEN"
            else:
                try:
                    args = REGISTRY[name][0].model_validate(tc["args"]).model_dump()
                    if name == "order.lookup":
                        found = c.execute(
                            select(s.orders.c.id, s.orders.c.shipping).where(
                                s.orders.c.tenant == t,
                                s.orders.c.id == args["order_id"],
                                s.orders.c.customer_id == se.customer_id,
                                s.orders.c.product_id == a.product_id,
                            )
                        ).first()
                        if found:
                            order = {"order_id": found.id, "shipping": found.shipping}
                        else:
                            result["error"] = "ORDER_ACCESS_DENIED"
                    if not result["error"]:
                        result["data"] = asyncio.run(exchange(name, args, order))
                        result["status"] = "succeeded"
                except ValidationError:
                    result["error"] = "TOOL_INVALID_DATA"
                except (ExceptionGroup, ValueError, TimeoutError, OSError):
                    result.update(error="TOOL_UNAVAILABLE", status="failed")
            insert(
                c,
                s.tools,
                t,
                eid,
                run_id=r.id,
                tool=name,
                action_key=key,
                status=result["status"],
                outcome=result,
            )
            return result
