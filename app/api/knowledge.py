import hashlib
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, File, Form, Query, UploadFile
from app import knowledge as k, contracts as x
from app.db import reject
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


@router.post("/{id}/documents", status_code=202)
def upload_document(
    id: str,
    call: Write,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    source_note: Annotated[str | None, Form(max_length=1000)] = None,
):
    call.identity.require("manage_knowledge")
    raw = file.file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        reject(413, "FILE_TOO_LARGE", "文件超过 1 MiB")
    try:
        body = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        reject(415, "UNSUPPORTED_MEDIA", "需要 UTF-8 文本")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".txt", ".md"}:
        reject(415, "UNSUPPORTED_MEDIA", "仅接受有效 TXT / Markdown")
    k.validate_text(body)
    return call.execute(
        ("manage_knowledge",),
        lambda c, w: k.upload(c, w, id, title, source_note, body),
        [title, source_note, ext, hashlib.sha256(raw).hexdigest()],
        authorize=lambda c, w: k.book(c, w, id),
    )


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
