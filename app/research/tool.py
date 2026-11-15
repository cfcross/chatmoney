"""The registered MCP server is bound to one verified job. Model input cannot choose another server or target."""

from datetime import timedelta
import anyio
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from app.research import network
from app.research.extract import parse
from app.research.contracts import FetchArgs, ProfileResult

from pydantic import BaseModel


class ToolReply(BaseModel):
    data: ProfileResult | None = None
    error: str | None = None


async def exchange(ticket, guard, audit):
    server = FastMCP("ChatMoney approved research")

    @server.tool(name=network.TOOL)
    async def read(company_url: str) -> ToolReply:
        if company_url != ticket["target"]:
            return ToolReply(error="SOURCE")
        try:
            return ToolReply(
                data=ProfileResult.model_validate(
                    await network.crawl(
                        company_url,
                        ticket["scope"],
                        parse,
                        guard=guard,
                        audit=audit,
                        **ticket["limits"],
                    )
                )
            )
        except network.Denied as e:
            return ToolReply(error=e.code)

    result = None
    with anyio.fail_after(ticket["limits"]["timeout"] + 12):
        async with create_connected_server_and_client_session(
            server,
            read_timeout_seconds=timedelta(seconds=ticket["limits"]["timeout"] + 13),
        ) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            if names == {network.TOOL}:
                resp = await c.call_tool(
                    network.TOOL, FetchArgs(company_url=ticket["target"]).model_dump()
                )
                if not resp.isError:
                    result = resp.structuredContent
    data = result or {}
    if "result" in data:
        data = data["result"]
    if not data.get("data"):
        raise network.Denied(data.get("error") or "NETWORK")
    return ProfileResult.model_validate(data["data"]).model_dump()
