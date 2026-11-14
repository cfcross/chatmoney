from typing import Literal
from fastapi import APIRouter, Query
from app import catalog as cat, contracts as x
from app.http import Read, Write

router = APIRouter(prefix="/agents", tags=["Agent 配置"])


@router.post("", status_code=201)
def create_agent(body: x.AgentCreate, call: Write):
    return call.execute(
        ("config", "own_agents"),
        lambda c, w: cat.create(c, w, body),
        body.model_dump(),
        authorize=lambda c, w: w.product(body.product_id),
    )


@router.get("/{agent_id}")
def get_agent(
    agent_id: str,
    call: Read,
    view: Literal["published", "draft"] | None = None,
    version: int | None = Query(None, ge=1),
):
    return call.execute(
        ("config", "publish", "business"),
        lambda c, w: cat.get(c, w, agent_id, view, version),
    )


@router.patch("/{agent_id}")
def patch_agent(agent_id: str, body: x.AgentPatch, call: Write):
    return call.execute(
        ("config", "own_agents"),
        lambda c, w: cat.revise(c, w, agent_id, body),
        body.model_dump(),
        authorize=lambda c, w: cat.agent(c, w, agent_id),
    )


@router.post("/{agent_id}/publish", status_code=201)
def publish_agent(agent_id: str, body: x.Publish, call: Write):
    return call.execute(
        ("publish", "own_agents"),
        lambda c, w: cat.publish(c, w, agent_id, body),
        body.model_dump(),
        authorize=lambda c, w: cat.agent(c, w, agent_id),
    )


@router.post("/{agent_id}/disable")
def disable_agent(agent_id: str, body: x.Reason, call: Write):
    return call.execute(
        ("publish", "own_agents"),
        lambda c, w: cat.disable(c, w, agent_id, body.reason),
        body.model_dump(),
        authorize=lambda c, w: cat.agent(c, w, agent_id),
    )
