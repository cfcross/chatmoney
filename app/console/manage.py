"""Workbench edits supplement the published V1 engine contract."""

from fastapi import APIRouter
from pydantic import Field

from app import catalog, control, knowledge
from app.contracts import Model, Pos, Short
from app.http import Write

router = APIRouter(prefix="/console/manage", include_in_schema=False)


class Rename(Model):
    name: Short
    expected_revision: Pos


class EditTask(Rename):
    note: str | None = Field(default=None, max_length=2000)


class EditDocument(Model):
    title: Short
    text: str = Field(min_length=1, max_length=1048576)
    source_note: str | None = Field(default=None, max_length=1000)
    expected_revision: Pos


@router.patch("/knowledge-bases/{id}")
def rename_book(id: str, body: Rename, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: knowledge.rename_book(c, w, id, body),
        body.model_dump(),
        authorize=lambda c, w: knowledge.book(c, w, id),
    )


@router.delete("/knowledge-bases/{id}")
def delete_book(id: str, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: knowledge.remove_book(c, w, id),
        authorize=lambda c, w: knowledge.book(c, w, id, history=True),
    )


@router.patch("/knowledge-bases/{id}/documents/{doc}", status_code=202)
def update_document(id: str, doc: str, body: EditDocument, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: knowledge.edit_document(c, w, id, doc, body),
        body.model_dump(exclude_unset=True),
        authorize=lambda c, w: knowledge.document(c, w, id, doc),
    )


@router.delete("/knowledge-bases/{id}/documents/{doc}")
def delete_document(id: str, doc: str, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: knowledge.remove_document(c, w, id, doc),
        authorize=lambda c, w: knowledge.document(c, w, id, doc, include_deleted=True),
    )


@router.patch("/tasks/{id}")
def update_task(id: str, body: EditTask, call: Write):
    return call.execute(
        ("own_agents", "operator"),
        lambda c, w: control.edit_task(c, w, id, body),
        body.model_dump(exclude_unset=True),
        authorize=lambda c, w: control.task_access(c, w, id),
    )


@router.delete("/tasks/{id}")
def delete_task(id: str, call: Write):
    return call.execute(
        ("own_agents", "operator"),
        lambda c, w: control.remove_task(c, w, id),
        authorize=lambda c, w: control.task_access(c, w, id, history=True),
    )


@router.delete("/agents/{id}")
def delete_agent(id: str, call: Write):
    return call.execute(
        ("own_agents", "publish"),
        lambda c, w: control.remove_agent(c, w, id),
        authorize=lambda c, w: catalog.agent(c, w, id, history=True),
    )
