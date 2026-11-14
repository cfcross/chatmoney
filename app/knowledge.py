import re
from sqlalchemy import select, or_, func, cast, Text
from sqlalchemy.dialects.postgresql import ARRAY
from app import schema as s
from app.db import insert, now, reject, row, uid, update
from app.work import enqueue, emit


def book(c, w, id, *, history=False):
    k = row(c, s.kbs, w.tenant, id)
    w.product(k.product_id)
    if k.deleted_at and not history:
        reject(404, "NOT_FOUND", "资料库不存在或已删除")
    return k


def create(c, w, b):
    row(c, s.products, w.tenant, w.product(b.product_id))
    k = insert(c, s.kbs, w.tenant, uid("kb"), name=b.name, product_id=b.product_id)
    return 201, {"kb_id": k, "product_id": b.product_id, "latest_snapshot": None}


def upload(c, w, id, title, source, body, *, file_id=None, revision=1):
    kb = book(c, w, id)
    doc = insert(
        c,
        s.docs,
        w.tenant,
        uid("doc"),
        kb_id=id,
        file_id=file_id,
        revision=revision,
        title=title,
        original=body,
        status="processing",
        source_note=source,
        actor=w.actor,
    )
    run = enqueue(c, w.tenant, kb.product_id, "index", payload={"document_id": doc})
    return 202, dict(document_id=doc, run_id=run, status="queued")


def index(c, t, id):
    d = row(c, s.docs, t, id)
    if d.status in {"withdrawn", "deleted"}:
        return {"action": "suppressed", "reason": d.status}
    if d.status == "processing":
        paragraphs = re.split(r"\n\s*\n|(?=^#{1,6} )", d.original, flags=re.M)
        pieces = (
            part[i : i + 700].strip()
            for part in paragraphs
            for i in range(0, len(part), 700)
        )
        for n, body in enumerate(filter(None, pieces), 1):
            insert(c, s.chunks, t, f"{id}:{n}", document_id=id, ordinal=n, body=body)
        update(c, s.docs, t, id, status="ready")
    return {"action": "indexed", "document_id": id, "revision": d.revision}


def publish(c, w, id, b):
    k = book(c, w, id)
    if k.latest != (b.expected_snapshot or 0):
        reject(409, "REVISION_CONFLICT", "资料快照已变更")
    refs = [v.model_dump() for v in b.documents]
    approved = (
        {
            d["document_id"]
            for d in row(c, s.snapshots, w.tenant, f"{id}:{k.latest}").documents
        }
        if k.latest
        else set()
    )
    files = set()
    for ref in refs:
        d = row(c, s.docs, w.tenant, ref["document_id"])
        if (
            d.kb_id != id
            or d.status != "ready"
            or (not d.is_current and d.id not in approved)
            or d.revision != ref["revision"]
        ):
            reject(409, "DOCUMENT_NOT_READY", "修订未就绪")
        file_id = d.file_id or d.id
        if file_id in files:
            reject(422, "VALIDATION_ERROR", "每份文件只能选择一个修订。")
        files.add(file_id)
    n = k.latest + 1
    insert(
        c,
        s.snapshots,
        w.tenant,
        f"{id}:{n}",
        kb_id=id,
        number=n,
        documents=refs,
        actor=w.actor,
    )
    update(c, s.kbs, w.tenant, id, latest=n)
    return 201, dict(kb_id=id, snapshot=n, status="published")


def get(c, w, id, snapshot):
    kb = book(c, w, id)
    q = select(s.docs).where(
        s.docs.c.tenant == w.tenant, s.docs.c.kb_id == id, s.docs.c.status != "deleted"
    )
    editor = w.manage_knowledge
    if snapshot or not editor:
        n = snapshot or kb.latest
        docs = row(c, s.snapshots, w.tenant, f"{id}:{n}").documents if n else []
        q = q.where(s.docs.c.id.in_([d["document_id"] for d in docs]))
    else:
        q = q.where(s.docs.c.is_current)
    if not editor:
        q = q.where(s.docs.c.status == "ready")
    return 200, dict(
        kb_id=id,
        name=kb.name,
        edit_revision=kb.edit_revision,
        latest_snapshot=kb.latest or None,
        documents=[
            dict(
                document_id=d.id,
                file_id=d.file_id or d.id,
                title=d.title,
                status=d.status,
                revision=d.revision,
            )
            for d in c.execute(q).mappings()
        ],
    )


def withdraw(c, w, id, b):
    k = book(c, w, id)
    doc = row(c, s.docs, w.tenant, b.document_id)
    if doc.kb_id != id or doc.revision != b.revision or doc.status == "deleted":
        reject(404, "NOT_FOUND", "修订不可见")
    update(c, s.docs, w.tenant, doc.id, status="withdrawn")
    cancel_source_sends(c, w.tenant, k.product_id, [doc.id])
    insert(
        c,
        s.audit,
        w.tenant,
        uid("au"),
        actor=w.actor,
        operation="withdraw",
        resource=doc.id,
        detail={"reason": b.reason},
    )
    return 200, dict(document_id=doc.id, revision=doc.revision, status="withdrawn")


def cancel_source_sends(c, tenant, product, doc_ids):
    if not doc_ids:
        return
    for send in c.execute(
        select(s.sends).where(
            s.sends.c.tenant == tenant,
            s.sends.c.status == "pending",
            s.sends.c.sources.op("?|")(cast(doc_ids, ARRAY(Text))),
        )
    ).mappings():
        update(c, s.sends, tenant, send.id, status="cancelled")
        emit(
            c,
            tenant,
            product,
            "delivery.updated",
            sid=send.session_id,
            run=send.run_id,
            data={"send_id": send.id, "status": "cancelled"},
        )


def retrieve(c, t, product, cfg, question, *, product_overview=False):
    ids = set()
    for ref in cfg["knowledge"]:
        k = row(c, s.kbs, t, ref["kb_id"])
        if k.deleted_at or k.product_id != product:
            continue
        ids.update(
            x["document_id"]
            for x in row(c, s.snapshots, t, f"{k.id}:{ref['snapshot']}").documents
        )
    # Chinese character pairs preserve product terms without installing a separate tokenizer.
    words = re.findall(r"[a-zA-Z0-9]+|[\u3400-\u9fff]+", question.lower())
    grams = list(
        dict.fromkeys(
            g
            for w in words
            for g in (
                [w]
                if w.isascii() or len(w) < 3
                else [w[i : i + 2] for i in range(len(w) - 1)]
            )
        )
    )[:64]
    d, ch = s.docs, s.chunks
    query = (
        select(ch.c.id, ch.c.body, d.c.id.label("document_id"))
        .join(d, (ch.c.tenant == d.c.tenant) & (ch.c.document_id == d.c.id))
        .where(ch.c.tenant == t, d.c.status == "ready", d.c.id.in_(ids))
    )
    if product_overview:
        return [
            dict(v)
            for v in c.execute(
                query.order_by(d.c.title, ch.c.ordinal).limit(3)
            ).mappings()
        ]
    if not grams:
        return []
    from sqlalchemy import case

    match = [
        (func.strpos(func.lower(ch.c.body + " " + d.c.title), g) > 0) for g in grams
    ]
    query = (
        query.where(or_(*match))
        .order_by(sum(case((m, 1), else_=0) for m in match).desc(), ch.c.id)
        .limit(3)
    )
    return [dict(v) for v in c.execute(query).mappings()]


def rename_book(c, w, id, body):
    kb = book(c, w, id)
    if kb.edit_revision != body.expected_revision:
        reject(409, "REVISION_CONFLICT", "资料库名称已经改变，请刷新。")
    update(c, s.kbs, w.tenant, id, name=body.name, edit_revision=kb.edit_revision + 1)
    return 200, {"kb_id": id, "name": body.name, "edit_revision": kb.edit_revision + 1}


def document(c, w, kb_id, doc_id, *, include_deleted=False):
    kb = book(c, w, kb_id)
    d = row(c, s.docs, w.tenant, doc_id)
    if d.kb_id != kb.id or (d.status == "deleted" and not include_deleted):
        reject(404, "NOT_FOUND", "文件不存在或不可见")
    return kb, d


def edit_document(c, w, kb_id, doc_id, body):
    _, d = document(c, w, kb_id, doc_id)
    if not d.is_current or d.revision != body.expected_revision:
        reject(409, "REVISION_CONFLICT", "文件已更新，请打开最新修订。")
    validate_text(body.text)
    update(c, s.docs, w.tenant, d.id, is_current=False)
    source = (
        body.source_note if "source_note" in body.model_fields_set else d.source_note
    )
    status, result = upload(
        c,
        w,
        kb_id,
        body.title,
        source,
        body.text,
        file_id=d.file_id or d.id,
        revision=d.revision + 1,
    )
    return status, result | {"revision": d.revision + 1}


def validate_text(body):
    if len(body.encode("utf-8")) > 1024 * 1024:
        reject(413, "FILE_TOO_LARGE", "每个文件最多 1 MiB。")
    if not body.strip() or any(ord(x) < 32 and x not in "\t\n\r" for x in body):
        reject(415, "UNSUPPORTED_MEDIA", "仅接受有效的 UTF-8 文本。")


def remove_documents(c, tenant, kb, condition):
    ids = c.scalars(
        s.docs.update()
        .where(
            s.docs.c.tenant == tenant,
            s.docs.c.kb_id == kb.id,
            s.docs.c.status != "deleted",
            condition,
        )
        .values(status="deleted")
        .returning(s.docs.c.id)
    ).all()
    cancel_source_sends(c, tenant, kb.product_id, ids)


def remove_document(c, w, kb_id, doc_id):
    kb, d = document(c, w, kb_id, doc_id, include_deleted=True)
    if d.status != "deleted":
        if not d.is_current:
            reject(409, "REVISION_CONFLICT", "文件已修改，请核对最新版本后再删除。")
        remove_documents(
            c,
            w.tenant,
            kb,
            func.coalesce(s.docs.c.file_id, s.docs.c.id) == (d.file_id or d.id),
        )
    return 200, {"document_id": doc_id, "deleted": True}


def remove_book(c, w, id):
    kb = book(c, w, id, history=True)
    if not kb.deleted_at:
        remove_documents(c, w.tenant, kb, True)
        update(c, s.kbs, w.tenant, id, deleted_at=now())
    return 200, {"kb_id": id, "deleted": True}
