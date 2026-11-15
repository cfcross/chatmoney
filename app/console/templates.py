"""Authenticated workbench template operations. Core V1 routes keep their existing contract."""

from typing import Literal

from fastapi import APIRouter, Query

from app import agent_templates as library
from app.http import Read, Write

router = APIRouter(prefix="/console/manage", include_in_schema=False)
READ = ("personal_models", "config", "publish", "business")
WRITE = ("config", "own_agents")


@router.get("/templates")
def index(call: Read, kind: Literal["system", "personal"]):
    return call.execute(READ, lambda c, w: (200, library.directory(c, w, kind)))


@router.get("/templates/{id}")
def detail(id: str, call: Read):
    return call.execute(READ, lambda c, w: (200, library.get(c, w, id)))


@router.post("/templates", status_code=201)
def create(body: library.TemplateContent, call: Write):
    return call.execute(
        WRITE, lambda c, w: library.create(c, w, body), body.model_dump()
    )


@router.patch("/templates/{id}")
def edit(id: str, body: library.EditTemplate, call: Write):
    return call.execute(
        WRITE, lambda c, w: library.edit(c, w, id, body), body.model_dump()
    )


@router.delete("/templates/{id}")
def delete(id: str, call: Write, expected_version: int = Query(ge=1)):
    return call.execute(
        WRITE,
        lambda c, w: library.delete(c, w, id, expected_version),
        {"expected_version": expected_version},
    )


@router.post("/templates/{id}/copy", status_code=201)
def copy(id: str, body: library.CopyTemplate, call: Write):
    return call.execute(
        WRITE, lambda c, w: library.copy(c, w, id, body), body.model_dump()
    )


@router.get("/agents/{id}/template-preview")
def preview(
    id: str, view: Literal["draft", "published"], call: Read, version: int = Query(ge=1)
):
    return call.execute(
        WRITE, lambda c, w: (200, library.exportable(c, w, id, view, version))
    )


@router.post("/agents/{id}/templates", status_code=201)
def export(id: str, body: library.ExportTemplate, call: Write):
    return call.execute(
        WRITE, lambda c, w: library.export(c, w, id, body), body.model_dump()
    )
