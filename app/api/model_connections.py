"""Administrative API with its own OpenAPI contract alongside the original 29 operations."""

from typing import Annotated, Literal
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from pydantic import Field, SecretStr, field_validator, model_validator

from app import model_connections as service
from app.contracts import Model, Pos, Short
from app.db import Problem
from app.http import Read, Write
from app.llm import credentials, ModelError
from app.llm.deepseek import ERROR_MESSAGES

PREFIX = "/api/model-admin/v1"
PERSONAL = ("personal_models",)
api = FastAPI(
    title="ChatMoney 个人模型连接", version="1.1", docs_url=None, redoc_url=None
)
Key = Annotated[SecretStr, Field(min_length=1, max_length=512)]


class SecretInput(Model):
    @field_validator("api_key", check_fields=False)
    @classmethod
    def valid_key(cls, value):
        if value is not None:
            raw = value.get_secret_value()
            if (
                not raw.isascii()
                or not raw.isprintable()
                or any(c.isspace() for c in raw)
            ):
                raise ValueError("密钥格式无效")
        return value

    @model_validator(mode="after")
    def name_is_public(self):
        if self.name and self.api_key and self.api_key.get_secret_value() in self.name:
            raise ValueError("请单独填写连接名称和密钥")
        return self


class Create(SecretInput):
    name: Short
    api_key: Key


class Change(SecretInput):
    expected_revision: Pos
    name: Short | None = None
    api_key: Key | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.name is None and self.api_key is None:
            raise ValueError("请填写新名称或密钥")
        return self


class Revision(Model):
    expected_revision: Pos


class Disable(Revision):
    confirm_in_use: bool = False


class Connection(Model):
    id: str
    name: str
    provider: Literal["deepseek"]
    default_model: str
    supported_models: list[str]
    status: Literal["pending", "ready", "failed", "disabled"]
    revision: int
    last_tested_at: str | None
    last_error_code: str | None
    error_message: str | None
    created_by: str
    created: str
    updated: str


class Reply[T](Model):
    request_id: str
    data: T


class Usage(Model):
    agent_id: str
    name: str
    draft: bool
    versions: list[int]


class Deleted(Model):
    id: str
    deleted: bool


@api.get("/docs", include_in_schema=False)
def docs():
    return get_swagger_ui_html(
        openapi_url=PREFIX + "/openapi.json",
        title=api.title,
        swagger_js_url="/_docs/swagger-ui-bundle.js",
        swagger_css_url="/_docs/swagger-ui.css",
        swagger_favicon_url="/_docs/favicon-32x32.png",
    )


@api.middleware("http")
async def transport_guard(request: Request, call_next):
    # Reject before reading the body. The local listeners disable proxy headers.
    if request.scope["query_string"]:
        result = JSONResponse(
            {
                "request_id": request.state.rid,
                "error": {
                    "code": "QUERY_NOT_ALLOWED",
                    "message": "模型连接接口不接收网址参数。",
                    "retryable": False,
                },
            },
            400,
        )
    elif request.method in {"POST", "PATCH", "DELETE"} and (
        request.scope["scheme"] != "https" or "x-forwarded-proto" in request.headers
    ):
        result = JSONResponse(
            {
                "request_id": request.state.rid,
                "error": {
                    "code": "HTTPS_REQUIRED",
                    "message": "请在 HTTPS 工作台管理密钥。",
                    "retryable": False,
                },
            },
            403,
        )
    else:
        result = await call_next(request)
    result.headers["Cache-Control"] = "no-store"
    result.headers["Referrer-Policy"] = "no-referrer"
    return result


@api.exception_handler(RequestValidationError)
async def invalid_secret(request, err):
    # Validation details, including attacker-supplied field names, must never be reflected.
    return safe_error(
        request, 422, "VALIDATION_ERROR", "请检查名称、密钥、版本及必填字段。"
    )


def safe_error(request, status, code, message):
    return JSONResponse(
        {
            "request_id": request.state.rid,
            "error": {"code": code, "message": message, "retryable": False},
        },
        status_code=status,
    )


@api.exception_handler(Problem)
async def problem(request, err):
    return safe_error(request, err.status, err.code, err.message)


@api.exception_handler(Exception)
async def admin_failure(request, err):
    import logging

    logging.getLogger("chatmoney.models").error(
        "request=%s error_type=%s", request.state.rid, type(err).__name__
    )
    return safe_error(
        request, 500, "INTERNAL_ERROR", "连接管理暂时不可用，请凭请求编号联系管理员。"
    )


@api.exception_handler(ModelError)
async def encryption_problem(request, err):
    return safe_error(request, 503, err.code, ERROR_MESSAGES[err.code])


def owns(id):
    return lambda c, w: service.read(c, w.tenant, w.actor, id)


def write_secret(call, body, fn, *, id=None):
    call.identity.require(*PERSONAL)
    fingerprint = body.model_dump(exclude={"api_key"})
    if body.api_key is not None:
        fingerprint["credential_digest"] = credentials.fingerprint(
            body.api_key.get_secret_value()
        )
    return call.execute(PERSONAL, fn, fingerprint, authorize=owns(id) if id else None)


@api.post("/connections", status_code=201, response_model=Reply[Connection])
def create_connection(body: Create, call: Write):
    return write_secret(call, body, lambda c, w: service.create(c, w, body))


@api.get("/connections", response_model=Reply[list[Connection]])
def connections(call: Read):
    return call.execute(
        PERSONAL, lambda c, w: (200, service.list_connections(c, w.tenant, w.actor))
    )


@api.get("/options", response_model=Reply[list[Connection]])
def options(call: Read):
    return call.execute(
        PERSONAL,
        lambda c, w: (
            200,
            service.list_connections(c, w.tenant, w.actor, ready_only=True),
        ),
    )


@api.patch("/connections/{id}", response_model=Reply[Connection])
def change_connection(id: str, body: Change, call: Write):
    return write_secret(call, body, lambda c, w: service.revise(c, w, id, body), id=id)


@api.post("/connections/{id}/test", response_model=Reply[Connection])
def verify_connection(id: str, body: Revision, call: Write):
    # Serialize tests on this connection without holding the entire tenant's send/ownership fence.
    return call.execute(
        PERSONAL,
        lambda c, w: service.test_connection(c, w, id, body.expected_revision),
        body.model_dump(),
        authorize=owns(id),
        fence=("model-test", call.identity.tenant, id),
    )


@api.get("/connections/{id}/usage", response_model=Reply[list[Usage]])
def connection_usage(id: str, call: Read):
    return call.execute(PERSONAL, lambda c, w: (200, service.usage(c, w, id)))


@api.post("/connections/{id}/disable", response_model=Reply[Connection])
def disable_connection(id: str, body: Disable, call: Write):
    return call.execute(
        PERSONAL,
        lambda c, w: service.disable(c, w, id, body),
        body.model_dump(),
        authorize=owns(id),
    )


@api.delete("/connections/{id}", response_model=Reply[Deleted])
def delete_connection(id: str, body: Revision, call: Write):
    return call.execute(
        PERSONAL,
        lambda c, w: service.remove(c, w, id, body.expected_revision),
        body.model_dump(),
    )
