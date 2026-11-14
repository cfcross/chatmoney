import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import create_engine, select, text
from app.settings import settings


class Problem(Exception):
    def __init__(self, status, code, message, retryable=False):
        self.status, self.code, self.message, self.retryable = (
            status,
            code,
            message,
            retryable,
        )
        super().__init__(code)


def reject(status, code, message):
    raise Problem(status, code, message)


def now():
    return datetime.now(UTC)


def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


@lru_cache
def engine():
    return create_engine(
        settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )


def scope(conn, tenant):
    conn.execute(
        text("select set_config('app.tenant', :tenant, true)"), {"tenant": tenant}
    )


def model_scope(conn, actor):
    conn.execute(
        text("select set_config('app.model_actor', :actor, true)"),
        {"actor": actor or ""},
    )


def lock(conn, *parts):
    import json

    conn.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": json.dumps(parts, ensure_ascii=False)},
    )


@contextmanager
def transaction(tenant, *, model_actor=None):
    with engine().begin() as conn:
        scope(conn, tenant)
        if model_actor is not None:
            model_scope(conn, model_actor)
        yield conn


def row(conn, table, tenant, id, *, locked=False):
    query = select(table).where(table.c.tenant == tenant, table.c.id == id)
    item = conn.execute(query.with_for_update() if locked else query).mappings().first()
    if item is None:
        reject(404, "NOT_FOUND", "资源不存在或不可见")
    return item


def update(conn, table, tenant, id, **values):
    conn.execute(
        table.update()
        .where(table.c.tenant == tenant, table.c.id == id)
        .values(**values)
    )


def insert(conn, table, tenant, id, **values):
    conn.execute(table.insert().values(tenant=tenant, id=id, **values))
    return id
