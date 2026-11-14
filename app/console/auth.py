"""Password login and tenant-scoped enrollment for the first-party workbench."""

from typing import Annotated, Literal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from app import web_identity as web
from app.db import engine, reject, transaction
from app.http import Read

router = APIRouter(prefix="/console/auth", include_in_schema=False)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: Annotated[SecretStr, Field(min_length=1, max_length=256)]


class Login(Credentials):
    new_username: str | None = Field(default=None, min_length=2, max_length=64)
    new_password: Annotated[SecretStr, Field(min_length=1, max_length=256)] | None = (
        None
    )


class Activation(Credentials):
    setup_code: SecretStr = Field(min_length=1, max_length=128)
    new_username: str | None = Field(default=None, min_length=2, max_length=64)


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=2, max_length=64)
    existing_actor: str | None = Field(default=None, min_length=1, max_length=120)
    delivery: Literal["link", "temporary_password", "password"] = "link"
    password: SecretStr | None = Field(default=None, max_length=256)


class MemberEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: SecretStr | None = Field(default=None, max_length=256)


class SelfEdit(MemberEdit):
    current_password: SecretStr = Field(min_length=1, max_length=256)


class IssuerGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


def guard(request):
    web.browser_transport(request)
    web.same_origin(request)
    if request.scope["query_string"]:
        reject(400, "QUERY_NOT_ALLOWED", "请使用工作台表单。")


def session_reply(request, body, setup=None, replacement=None):
    guard(request)
    name = web.username(body.username)
    web.limit_attempt(name, request.client.host if request.client else "unknown")
    if body.new_username and not setup and replacement is None:
        reject(422, "PASSWORD_REQUIRED", "首次修改账号时请同时设置个人密码。")
    with web.unique_login():
        result = web.activate_or_login(
            name,
            body.password.get_secret_value(),
            setup,
            request.cookies.get(web.COOKIE),
            replacement=replacement,
            new_username=web.username(body.new_username) if body.new_username else None,
        )
    if result.change_password:
        return JSONResponse(
            {
                "data": {"signed_in": False, "password_change_required": True},
                "request_id": request.state.rid,
            }
        )
    reply = JSONResponse({"data": {"signed_in": True}, "request_id": request.state.rid})
    reply.set_cookie(
        web.COOKIE,
        result.session,
        max_age=int(web.SESSION_LIFE.total_seconds()),
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return reply


@router.post("/login")
def login(request: Request, body: Login):
    replacement = (
        body.new_password.get_secret_value() if body.new_password is not None else None
    )
    return session_reply(request, body, replacement=replacement)


@router.post("/activate")
def activate(request: Request, body: Activation):
    return session_reply(request, body, body.setup_code.get_secret_value())


@router.post("/logout")
def logout(request: Request):
    guard(request)
    with engine().begin() as c:
        web.revoke_cookie(c, request.cookies.get(web.COOKIE), request=request)
    reply = JSONResponse(
        {"data": {"signed_in": False}, "request_id": request.state.rid}
    )
    reply.delete_cookie(
        web.COOKIE, path="/", secure=True, httponly=True, samesite="strict"
    )
    return reply


@router.get("/members")
def members(request: Request, call: Read):
    who = call.identity
    guard(request)
    with transaction(who.tenant) as c:
        if not web.can_issue_members(c, who):
            reject(403, "FORBIDDEN", "当前账号没有本企业的开通账号权限。")
        items = web.existing_members(c, who)
    return {"data": {"items": items}, "request_id": request.state.rid}


@router.post("/members")
def invite(request: Request, body: Member, call: Read):
    who = call.identity
    guard(request)
    if (body.delivery == "password") != (body.password is not None):
        reject(422, "PASSWORD_REQUIRED", "请为成员填写登录密码。")
    with web.unique_login():
        with transaction(who.tenant) as c:
            result = web.issue_member(
                c,
                who,
                web.username(body.username),
                body.existing_actor,
                temporary_password=body.delivery == "temporary_password",
                password=body.password.get_secret_value()
                if body.password is not None
                else None,
            )
    return {"data": result, "request_id": request.state.rid}


@router.post("/members/{actor_id}/issuer")
def issuer(request: Request, actor_id: str, body: IssuerGrant, call: Read):
    guard(request)
    who = call.identity
    with transaction(who.tenant) as c:
        data = web.set_issuer(c, who, actor_id, body.enabled)
    return {"data": data, "request_id": request.state.rid}


@router.patch("/members/{actor_id}")
def edit_member(request: Request, actor_id: str, body: MemberEdit, call: Read):
    guard(request)
    who = call.identity
    with web.unique_login(), transaction(who.tenant) as c:
        result = web.edit_credentials(
            c,
            who,
            actor_id,
            body.username,
            body.password.get_secret_value() if body.password is not None else None,
        )
    return {"data": result, "request_id": request.state.rid}


@router.patch("/me")
def edit_self(request: Request, body: SelfEdit, call: Read):
    guard(request)
    who = call.identity
    web.limit_attempt(
        "edit:" + who.actor, request.client.host if request.client else "unknown"
    )
    with web.unique_login(), transaction(who.tenant) as c:
        result = web.edit_credentials(
            c,
            who,
            who.actor,
            body.username,
            body.password.get_secret_value() if body.password is not None else None,
            current=body.current_password.get_secret_value(),
        )
        web.clear_limit(c, "edit:" + who.actor)
    reply = JSONResponse({"data": result, "request_id": request.state.rid})
    reply.delete_cookie(
        web.COOKIE, path="/", secure=True, httponly=True, samesite="strict"
    )
    return reply


@router.delete("/members/{actor_id}")
def delete_member(request: Request, actor_id: str, call: Read):
    guard(request)
    who = call.identity
    with transaction(who.tenant) as c:
        data = web.delete_member(c, who, actor_id)
    return {"data": data, "request_id": request.state.rid}
