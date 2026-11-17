import functools
from pathlib import Path
import anyio
from fastapi import APIRouter, Query, Request
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool
from app import knowledge as k, contracts as x
from app import knowledge_upload as up
from app.db import reject, transaction
from app.http import Read, Write

router = APIRouter(prefix="/knowledge-bases", tags=["知识资料"])


@router.post("", status_code=201)
def create_book(body: x.KBCreate, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: k.create(c, w, body),
        body.model_dump(),
        authorize=lambda c, w: w.product(body.product_id),
    )


@router.post("/{id}/documents", status_code=202, openapi_extra=up.UPLOAD_SCHEMA)
async def upload_document(id: str, request: Request, call: Write):
    call.identity.require("manage_knowledge")
    def check_book():
        with transaction(call.identity.tenant) as c:
            k.book(c, call.identity, id)
    await run_in_threadpool(check_book)
    slot = up.upload_slot(call.identity.tenant)
    await run_in_threadpool(slot.__enter__)
    try:
        form, size, digest = await up.receive_form(request)
        try:
            title = form.get("title", "")
            source = form.get("source_note") or None
            replace_id = form.get("replace_id") or None
            revision = form.get("expected_revision")
            if not title.strip() or len(title)>200 or (source and len(source)>1000):
                reject(422, "VALIDATION_ERROR", "请检查文件名称和资料来源。")
            if bool(replace_id) != bool(revision) or (revision and (not revision.isdecimal() or len(revision)>10)):
                reject(422, "VALIDATION_ERROR", "请核对需要替换的文件和版本。")
            file = form["file"]
            fingerprint = [title, source, Path(file.filename).suffix.lower(), digest]
            if replace_id:
                fingerprint += [replace_id, revision]
            return await run_in_threadpool(functools.partial(
                call.execute, ("manage_knowledge",),
                lambda c,w: k.save_file(c,w,id,title,source,file,size,digest,replace_id,int(revision or 0)),
                fingerprint, authorize=lambda c,w: k.book(c,w,id),
                fence=("knowledge-upload",call.identity.tenant)))
        finally:
            with anyio.CancelScope(shield=True):
                await form.close()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in {"53100","53400"}:
            reject(507, "STORAGE_FULL", up.SPACE_ERROR)
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(slot.__exit__, None, None, None)


@router.get("/{id}")
def read_book(id: str, call: Read, snapshot: int | None = Query(None, ge=1)):
    return call.execute(
        ("config", "publish", "business"), lambda c, w: k.get(c, w, id, snapshot)
    )


@router.post("/{id}/publish", status_code=201)
def publish_book(id: str, body: x.KBPublish, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: k.publish(c, w, id, body),
        body.model_dump(),
        authorize=lambda c, w: k.book(c, w, id),
    )


@router.post("/{id}/withdraw")
def withdraw_document(id: str, body: x.Withdraw, call: Write):
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: k.withdraw(c, w, id, body),
        body.model_dump(),
        authorize=lambda c, w: k.book(c, w, id),
    )
