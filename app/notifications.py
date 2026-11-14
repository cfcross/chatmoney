"""Durable business events and optional administrator-configured signed callbacks."""

from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as upsert
from app import schema as s
from app.db import now, transaction, update
from app.settings import settings


def envelope(e):
    return dict(
        event_id=e.id,
        type=e.type,
        at=e.created.isoformat(),
        session_id=e.session_id,
        task_id=e.task_id,
        run_id=e.run_id,
        data=e.data,
    )


def signature(secret, stamp, body):
    return hmac.new(
        secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def verify(secret, stamp, body, sig, current=None):
    try:
        return abs(
            (current or time.time()) - int(stamp)
        ) <= 300 and hmac.compare_digest(signature(secret, stamp, body), sig)
    except (ValueError, TypeError):
        return False


class Subscription(BaseModel):
    id: str
    tenant: str
    products: list[str]
    types: list[str]
    url: str
    secret: SecretStr


def deliver_events(subscriptions=None, transport=None):
    if subscriptions is None:
        path = settings().webhook_config_file
        if not path:
            return
        subscriptions = [
            Subscription.model_validate(x)
            for x in json.loads(Path(path).read_text(encoding="utf8"))
        ]
    for sub in subscriptions:
        parts = urlsplit(sub.url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError("invalid configured callback")
        t = sub.tenant
        with transaction(t) as c:
            jobs = s.webhooks
            sent = select(jobs.c.event_id).where(
                jobs.c.tenant == t, jobs.c.subscriber == sub.id
            )
            new = c.scalars(
                select(s.events.c.id)
                .where(
                    s.events.c.tenant == t,
                    s.events.c.product_id.in_(sub.products),
                    s.events.c.type.in_(sub.types),
                    s.events.c.id.not_in(sent),
                )
                .order_by(s.events.c.ordinal)
                .limit(10)
            ).all()
            for eid in new:
                c.execute(
                    upsert(jobs)
                    .values(
                        tenant=t,
                        id=sub.id + ":" + eid,
                        event_id=eid,
                        subscriber=sub.id,
                        status="pending",
                    )
                    .on_conflict_do_nothing()
                )
        with transaction(t) as c:
            job = (
                c.execute(
                    select(jobs)
                    .where(
                        jobs.c.tenant == t,
                        jobs.c.subscriber == sub.id,
                        jobs.c.status == "pending",
                        jobs.c.due <= now(),
                    )
                    .order_by(jobs.c.due)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not job:
                continue
            e = (
                c.execute(
                    select(s.events).where(
                        s.events.c.id == job.event_id, s.events.c.tenant == t
                    )
                )
                .mappings()
                .one()
            )
            if e.product_id not in sub.products or e.type not in sub.types:
                update(c, jobs, t, job.id, status="disabled")
                continue
            body = json.dumps(
                envelope(e), ensure_ascii=False, separators=(",", ":")
            ).encode()
            stamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "X-Event-Id": e.id,
                "X-Timestamp": stamp,
                "X-Signature": signature(sub.secret.get_secret_value(), stamp, body),
            }
            try:
                with httpx.Client(
                    transport=transport, timeout=3, follow_redirects=False
                ) as http:
                    ok = http.post(sub.url, content=body, headers=headers).is_success
            except httpx.HTTPError:
                ok = False
            n = job.attempt + 1
            update(
                c,
                jobs,
                t,
                job.id,
                status="delivered" if ok else ("failed" if n >= 5 else "pending"),
                attempt=n,
                due=now() + timedelta(seconds=min(2**n, 60)),
            )
