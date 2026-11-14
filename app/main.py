from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from app.db import Problem, uid
from app.api.agents import router as agents

from importlib.resources import files
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.staticfiles import StaticFiles

app = FastAPI(
    title="ChatMoney 共享 Agent Engine", version="1.1.0", docs_url=None, redoc_url=None
)
app.mount(
    "/_docs",
    StaticFiles(directory=str(files("swagger_ui") / "static")),
    name="swagger_assets",
)


@app.get("/docs", include_in_schema=False)
def swagger():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " | Swagger",
        swagger_js_url="/_docs/swagger-ui-bundle.js",
        swagger_css_url="/_docs/swagger-ui.css",
        swagger_favicon_url="/_docs/favicon-32x32.png",
    )


from app.api.knowledge import router as knowledge
from app.api.chat import router as chat
from app.api.control import router as control
from app.api.runtime import router as runtime

for router in (agents, knowledge, chat, control, runtime):
    app.include_router(router, prefix="/api/v1")
from app.api.openapi import configure

configure(app)

from app.console.routes import mount_console

mount_console(app)

from app.api.model_connections import PREFIX as model_prefix, api as model_admin

app.mount(model_prefix, model_admin)


@app.middleware("http")
async def request_id(request, call_next):
    request.state.rid = uid("req")
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.rid
    return response


@app.exception_handler(Problem)
async def problem(request: Request, exc: Problem):
    return JSONResponse(
        {
            "request_id": request.state.rid,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
    return await problem(
        request, Problem(422, "VALIDATION_ERROR", "字段无效：" + fields[:300])
    )


@app.exception_handler(OperationalError)
async def unavailable(request, exc):
    return await problem(request, Problem(503, "UNAVAILABLE", "数据库暂不可用", True))


@app.exception_handler(Exception)
async def unexpected(request, exc):
    import logging

    logging.getLogger("chatmoney.api").error(
        "request=%s error_type=%s", request.state.rid, type(exc).__name__
    )
    return await problem(
        request, Problem(500, "INTERNAL_ERROR", "内部错误，请凭请求编号查询")
    )
