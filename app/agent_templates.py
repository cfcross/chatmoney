"""Reusable assistant settings. Only the fields in RoleDefaults cross the instance boundary."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy import func, select

from app import schema as s
from app.contracts import BodyText, Model, Pos, Short, TemplateSource, Workflow
from app.db import insert, reject, uid, update
from app.policies import REGISTRY

Items = Annotated[list[Short], Field(max_length=32)]
SIGNALS = {workflow: set(signals) for workflow, signals in REGISTRY.values()}


class RoleDefaults(Model):
    persona: Short
    goal: BodyText
    tone: Short
    allowed: Items = Field(default_factory=list)
    forbidden: Items = Field(default_factory=list)
    handoff_on: Items = Field(default_factory=list)


class Requirements(Model):
    knowledge: bool = True
    tools: Items = Field(default_factory=list)
    handoff: Items = Field(default_factory=list)


class TemplateContent(Model):
    name: Short
    description: Short
    workflow: Workflow
    scenario: Short
    platforms: Items = Field(default_factory=list)
    audience: str = Field(default="", max_length=400)
    materials: str = Field(default="", max_length=400)
    recommended_tools: Items = Field(default_factory=list)
    human_review: str = Field(default="", max_length=600)
    config: RoleDefaults
    requirements: Requirements = Field(default_factory=Requirements)

    @model_validator(mode="after")
    def valid_handoff(self):
        enabled = set(self.config.handoff_on)
        if enabled - SIGNALS[self.workflow] or set(self.requirements.handoff) - enabled:
            raise ValueError("转人工条件与所选业务不符")
        return self


class EditTemplate(Model):
    expected_version: Pos
    content: TemplateContent


class CopyTemplate(Model):
    expected_version: Pos
    name: Short


class ExportTemplate(Model):
    # Exactly one source revision must be named. The editable preview has no bindings.
    view: Literal["draft", "published"]
    version: Pos
    content: TemplateContent


BUILTINS = {
    item["id"]: {
        "id": item["id"],
        "version": item["version"],
        "type": "system",
        **TemplateContent.model_validate(item["content"]).model_dump(),
    }
    for item in json.loads(
        Path(__file__).with_name("tob_templates.json").read_text(encoding="utf-8")
    )
}


def personal(row):
    return {
        "id": row.id,
        "type": "personal",
        "version": row.revision,
        "created_at": row.created.isoformat(),
        "updated_at": row.updated.isoformat(),
        **row.content,
    }


def owned(c, w, id):
    if id in BUILTINS:
        reject(403, "SYSTEM_TEMPLATE", "系统模板只读，可以先复制为我的模板。")
    rec = (
        c.execute(
            select(s.agent_templates).where(
                s.agent_templates.c.tenant == w.tenant,
                s.agent_templates.c.owner_id == w.actor,
                s.agent_templates.c.id == id,
            )
        )
        .mappings()
        .first()
    )
    if rec is None:
        reject(404, "NOT_FOUND", "模板不存在或当前账号不可见。")
    return rec


def get(c, w, id):
    return BUILTINS[id] if id in BUILTINS else personal(owned(c, w, id))


def directory(c, w, kind):
    if kind == "system":
        return list(BUILTINS.values())
    rows = c.execute(
        select(s.agent_templates)
        .where(
            s.agent_templates.c.tenant == w.tenant,
            s.agent_templates.c.owner_id == w.actor,
        )
        .order_by(s.agent_templates.c.updated.desc(), s.agent_templates.c.id)
    ).mappings()
    return [personal(row) for row in rows]


def check_version(rec, expected):
    if rec["version"] != expected:
        reject(409, "TEMPLATE_CHANGED", "模板已更新，请重新查看后再操作。")


def create(c, w, content):
    id = insert(
        c,
        s.agent_templates,
        w.tenant,
        uid("tpl"),
        owner_id=w.actor,
        content=content.model_dump(),
    )
    return 201, {"id": id, "version": 1}


def edit(c, w, id, body):
    rec = personal(owned(c, w, id))
    check_version(rec, body.expected_version)
    update(
        c,
        s.agent_templates,
        w.tenant,
        id,
        content=body.content.model_dump(),
        revision=rec["version"] + 1,
        updated=func.now(),
    )
    return 200, {"id": id, "version": rec["version"] + 1}


def delete(c, w, id, expected):
    check_version(personal(owned(c, w, id)), expected)
    c.execute(
        s.agent_templates.delete().where(
            s.agent_templates.c.tenant == w.tenant,
            s.agent_templates.c.owner_id == w.actor,
            s.agent_templates.c.id == id,
        )
    )
    return 200, {"id": id, "deleted": True}


def copy(c, w, id, body):
    rec = get(c, w, id)
    check_version(rec, body.expected_version)
    fields = {key: rec[key] for key in TemplateContent.model_fields}
    return create(c, w, TemplateContent.model_validate(fields | {"name": body.name}))


def apply(c, w, source: TemplateSource, workflow):
    rec = get(c, w, source.id)
    check_version(rec, source.version)
    if rec["workflow"] != workflow:
        reject(422, "TEMPLATE_WORKFLOW", "模板业务类型与助手不一致。")
    origin = {
        **source.model_dump(),
        "name": rec["name"],
        "requirements": rec["requirements"],
    }
    return origin, dict(rec["config"])


def exportable(c, w, id, view, number):
    from app import catalog

    agent = catalog.agent(c, w, id)
    if agent.owner_id != w.actor:
        reject(404, "NOT_FOUND", "只能从自己创建的助手保存模板。")
    if view == "draft":
        if number != agent.revision:
            reject(409, "REVISION_CONFLICT", "助手草稿已更新，请重新查看。")
        config = agent.draft
    else:
        config = catalog.version(c, w.tenant, id, number).config
    # Return editable defaults even for an unfinished source draft.
    defaults = {
        k: config.get(k, "" if k in ("persona", "tone", "goal") else [])
        for k in RoleDefaults.model_fields
    }
    return {
        "workflow": agent.workflow,
        "config": defaults,
        "requirements": (agent.template_origin or {}).get(
            "requirements", Requirements().model_dump()
        ),
    }


def export(c, w, id, body):
    preview = exportable(c, w, id, body.view, body.version)
    if preview["workflow"] != body.content.workflow:
        reject(422, "TEMPLATE_WORKFLOW", "请选择原助手的业务类型。")
    return create(c, w, body.content)
