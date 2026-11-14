import hashlib
import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from app import schema as s
from app.db import engine, insert, lock, reject, scope, model_scope, uid


@dataclass
class Identity:
    tenant: str
    actor: str
    roles: set
    products: set
    session_id: str | None = None

    @property
    def personal_agents(self):
        return "own_agents" in self.roles and not self.roles.intersection(
            {"config", "publish"}
        )

    @property
    def manage_knowledge(self):
        from app.web_identity import can_manage

        return can_manage(self.roles)

    @property
    def personal_models(self):
        # Machine-only inbox and business credentials cannot provision a person's key.
        return bool({"model_admin", "own_agents"} & self.roles) or bool(self.session_id)

    def require(self, *roles):
        if "manage_knowledge" in roles and self.manage_knowledge:
            return
        if "personal_models" in roles and self.personal_models:
            return
        if not self.roles.intersection(roles):
            reject(403, "FORBIDDEN", "身份缺少操作权限")

    def product(self, product):
        if product not in self.products:
            reject(404, "NOT_FOUND", "资源不存在或不可见")
        return product


def authenticate(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
):
    if credentials is None:
        from app.web_identity import (
            COOKIE,
            browser_transport,
            check_context,
            cookie_identity,
            same_origin,
        )

        raw = request.cookies.get(COOKIE)
        if not raw or request.headers.get("Authorization"):
            reject(401, "UNAUTHENTICATED", "请登录工作台，或使用已授权的接口身份。")
        browser_transport(request)
        same_origin(request)
        with engine().begin() as c:
            found = cookie_identity(c, raw)
        if not found:
            reject(401, "UNAUTHENTICATED", "登录已失效，请重新登录。")
        actor, sid = found
        check_context(request, sid)
        return Identity(
            actor.tenant, actor.id, set(actor.roles), set(actor.products), sid
        )
    digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    with engine().begin() as db:
        db.execute(
            text("SELECT set_config('app.credential_hash', :digest, true)"),
            {"digest": digest},
        )
        actor = (
            db.execute(
                select(s.identities).where(
                    s.identities.c.token_hash == digest, s.identities.c.active
                )
            )
            .mappings()
            .first()
        )
    if not actor:
        reject(401, "UNAUTHENTICATED", "身份无效")
    return Identity(actor.tenant, actor.id, set(actor.roles), set(actor.products))


@dataclass
class Call:
    request: Request
    identity: Identity
    key: str | None = None

    def execute(self, roles, fn, body=None, *, authorize=None, fence=None):
        who = self.identity
        who.require(*roles)
        with engine().begin() as c:
            scope(c, who.tenant)
            model_scope(c, who.actor)
            if self.key is None:
                status, data = fn(c, who)
            else:
                # Short transactions share a tenant fence with dispatch, control and event ordering.
                lock(c, *(fence or ("mutation", who.tenant)))
                if who.session_id:
                    from app.web_identity import require_current_login

                    require_current_login(c, who)
                if authorize:
                    authorize(c, who)
                path = self.request.url.path

                def digest(value):
                    return hashlib.sha256(
                        json.dumps(
                            value, sort_keys=True, ensure_ascii=False, default=str
                        ).encode()
                    ).hexdigest()

                key = digest([who.actor, self.request.method, path, self.key])
                fingerprint = digest(body)
                match = (
                    c.execute(
                        select(s.idempotency).where(
                            s.idempotency.c.tenant == who.tenant,
                            s.idempotency.c.id == key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if match:
                    if match.fingerprint != fingerprint:
                        reject(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应不同内容")
                    self.request.state.rid = match.response["request_id"]
                    return JSONResponse(match.response, status_code=match.status)
                status, data = fn(c, who)
                response = {"request_id": self.request.state.rid, "data": data}
                insert(
                    c,
                    s.idempotency,
                    who.tenant,
                    key,
                    actor=who.actor,
                    fingerprint=fingerprint,
                    status=status,
                    response=response,
                )
                insert(
                    c,
                    s.audit,
                    who.tenant,
                    uid("au"),
                    actor=who.actor,
                    operation=self.request.method,
                    resource=path,
                    detail={"request_id": self.request.state.rid},
                )
        return JSONResponse(
            {"request_id": self.request.state.rid, "data": data}, status_code=status
        )


def reading(request: Request, who: Annotated[Identity, Depends(authenticate)]):
    return Call(request, who)


def writing(
    request: Request,
    who: Annotated[Identity, Depends(authenticate)],
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    return Call(request, who, key)


Read = Annotated[Call, Depends(reading)]
Write = Annotated[Call, Depends(writing)]
VIEW = ("business", "seat", "observe")
CONFIG = ("config", "publish")
