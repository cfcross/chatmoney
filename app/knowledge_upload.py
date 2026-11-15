"""Bounded multipart intake and byte-exact, independently compressed PostgreSQL blocks."""
import codecs
import errno
import hashlib
import re
import shutil
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import PurePath
from time import monotonic

import anyio
from sqlalchemy import func, select, text
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from python_multipart.multipart import parse_options_header

from app import schema as s
from app.db import engine, reject
from app.settings import settings

FILE_LIMIT = 1_000_000_000
ENVELOPE = 65_536
BLOCK = 256 * 1024
INLINE = 1024 * 1024
INDEX_LIMIT = 2_000_000
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SPACE_ERROR = "服务器剩余空间不足，暂时无法上传。"
UPLOAD_SCHEMA = {"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {"type": "object", "required": ["title", "file"],
    "properties": {"title": {"type": "string", "maxLength": 200},
                   "file": {"type": "string", "format": "binary"},
                   "source_note": {"type": "string", "maxLength": 1000},
                   "replace_id": {"type": "string"}, "expected_revision": {"type": "integer"}}}}}}}


def size_error():
    reject(413, "FILE_TOO_LARGE", "文件超过 1 GB，请选择更小的文件。")


def space(required=0):
    if shutil.disk_usage(tempfile.gettempdir()).free < settings().upload_disk_reserve_bytes + required:
        reject(507, "STORAGE_FULL", SPACE_ERROR)


def filename_ok(name):
    return (name and len(name.encode("utf-8")) <= 255 and name not in (".", "..")
            and not any(c in name for c in '/\\:') and not re.search(r"[\x00-\x1f\x7f]", name)
            and PurePath(name).suffix.lower() in (".txt", ".md"))


@contextmanager
def upload_slot():
    # PostgreSQL owns the lock and releases it on connection loss, across API processes.
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        for slot in range(settings().upload_concurrency):
            key = {"slot": slot}
            if c.scalar(text("SELECT pg_try_advisory_lock(173184527,:slot)"), key):
                try:
                    yield
                finally:
                    c.execute(text("SELECT pg_advisory_unlock(173184527,:slot)"), key)
                return
        reject(429, "UPLOAD_BUSY", "已有文件正在上传，请稍后重试。")


class Intake(MultiPartParser):
    """Count file payload bytes as they arrive, before Starlette spools them."""
    def __init__(self, headers, stream):
        super().__init__(headers, stream, max_files=1, max_fields=4, max_part_size=4096)
        self.size = self.header_bytes = 0
        self.finished = self.has_text = False
        self.hash = hashlib.sha256()
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self.next_check = 0

    def on_header_field(self, data, start, end):
        self._check_header(end-start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self._check_header(end-start)
        super().on_header_value(data, start, end)

    def _check_header(self, amount):
        self.header_bytes += amount
        if self.header_bytes > 8192:
            reject(400, "INVALID_UPLOAD", "上传表单头过长。")

    def on_headers_finished(self):
        super().on_headers_finished()
        part = self._current_part
        if part.file:
            if part.field_name != "file" or not filename_ok(part.file.filename):
                reject(415, "UNSUPPORTED_MEDIA", "请选择文件名有效的 .txt 或 .md 文件。")
        elif part.field_name not in {"title", "source_note", "replace_id", "expected_revision"}:
            reject(422, "VALIDATION_ERROR", "上传表单包含未知字段。")

    def _validate(self, raw, final=False):
        try:
            decoded = self.decoder.decode(raw, final=final)
        except UnicodeDecodeError:
            reject(415, "UNSUPPORTED_MEDIA", "文件需要使用 UTF-8 编码。")
        if CONTROL.search(decoded):
            reject(415, "UNSUPPORTED_MEDIA", "文件含有不支持的控制字符。")
        if not self.has_text:
            self.has_text = bool(decoded.strip())

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.size += end-start
            if self.size > FILE_LIMIT:
                size_error()
            if self.size >= self.next_check:
                space(self.size * 2)
                self.next_check = self.size + 4 * 1024 * 1024
            raw = memoryview(data)[start:end]
            self.hash.update(raw)
            self._validate(raw)
        super().on_part_data(data, start, end)

    def on_part_end(self):
        if self._current_part.file is not None:
            self._validate(b"", final=True)
            if not self.has_text:
                reject(415, "UNSUPPORTED_MEDIA", "文件没有可用的文本。")
        super().on_part_end()

    def on_end(self):
        self.finished = True


async def receive_form(request):
    media, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary", b"")
    if media != b"multipart/form-data" or not 1 <= len(boundary) <= 70:
        reject(415, "UNSUPPORTED_MEDIA", "请用文件上传表单选择 TXT 或 Markdown。")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit() or len(length) > 20:
            reject(400, "INVALID_UPLOAD", "无效的上传长度。")
        if int(length) > FILE_LIMIT + ENVELOPE:
            size_error()
    space(min(FILE_LIMIT, int(length or 0)) * 3)
    async def stream():
        received = 0
        start = monotonic()
        source = request.stream().__aiter__()
        while True:
            try:
                with anyio.fail_after(min(120, max(0, 7200 - (monotonic()-start)))):
                    data = await anext(source)
            except StopAsyncIteration:
                return
            received += len(data)
            if received > FILE_LIMIT + ENVELOPE:
                size_error()
            for i in range(0, len(data), BLOCK):
                yield data[i:i+BLOCK]

    parser = Intake(request.headers, stream())
    try:
        form = await parser.parse()
        if not parser.finished or len(form) != len(form.multi_items()) or "file" not in form:
            await form.close()
            # An unfinished part is not in FormData. Close its spooled source as well.
            for raw_file in parser._files_to_close_on_error:
                raw_file.close()
            reject(422, "INVALID_UPLOAD", "文件上传不完整或字段重复，请重新上传。")
        return form, parser.size, parser.hash.hexdigest()
    except (ClientDisconnect, TimeoutError):
        reject(408, "UPLOAD_INTERRUPTED", "上传中断或超时，请重新上传。")
    except MultiPartException:
        reject(422, "INVALID_UPLOAD", "文件表单无效，请重新选择一份文件。")
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            reject(507, "STORAGE_FULL", SPACE_ERROR)
        raise


def check_quota(c, tenant, actor, incoming):
    d = s.docs.c
    size = func.coalesce(d.size_bytes, func.octet_length(d.original))
    total, personal = c.execute(select(func.sum(size), func.sum(size).filter(d.actor == actor))
        .where(d.tenant == tenant, d.status != "deleted")).one()
    cfg = settings()
    if (total or 0) + incoming > cfg.upload_workspace_bytes or (personal or 0) + incoming > cfg.upload_actor_bytes:
        reject(413, "STORAGE_QUOTA", "本账号或工作区的资料容量不足，请管理员整理旧文件。")


def write_blocks(c, tenant, doc, source):
    for i, data in enumerate(iter(lambda: source.read(BLOCK), b"")):
        c.execute(s.source_blocks.insert().values(tenant=tenant,id=f"{doc}:{i}",document_id=doc,
                  ordinal=i,payload=zlib.compress(data, level=1)))


def blocks(c, tenant, doc, start=0, count=None):
    b = s.source_blocks.c
    q = select(b.payload).where(b.tenant==tenant,b.document_id==doc,b.ordinal>=start).order_by(b.ordinal)
    if count is not None:
        q = q.limit(count)
    # A server cursor avoids fetching all blocks from PostgreSQL at once.
    with c.execute(q.execution_options(yield_per=2)) as result:
        for (payload,) in result:
            yield zlib.decompress(payload)
