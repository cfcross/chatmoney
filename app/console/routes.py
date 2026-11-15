import hashlib
from pathlib import Path

from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from app.console.read import router as reads
from app.console.auth import router as auth
from app.console.manage import router as manage
from app.console.templates import router as templates
from app.console.practice import router as practice

ASSETS = Path(__file__).parent / "static"


def asset_revision(directory):
    digest = hashlib.sha256()
    for asset in sorted(directory.iterdir()):
        if asset.is_file():
            digest.update(asset.name.encode() + b"\0")
            digest.update(hashlib.sha256(asset.read_bytes()).digest())
    return digest.hexdigest()[:16]


def mount_console(app):
    # All relative JS imports inherit this directory, including lazy-loaded pages.
    prefix = "/console/assets/v/" + asset_revision(ASSETS)
    app.mount(prefix, StaticFiles(directory=ASSETS), name="console-release-assets")
    # Allow already-open sessions to request their old asset URLs during an update.
    app.mount("/console/assets", StaticFiles(directory=ASSETS), name="console-assets")
    index = (ASSETS / "index.html").read_text(encoding="utf-8").replace(
        "/console/assets/", prefix + "/"
    )
    app.include_router(reads)
    app.include_router(auth)
    app.include_router(manage)
    app.include_router(templates)
    app.include_router(practice)
    from app.console.research import router as research
    app.include_router(research)

    @app.middleware("http")
    async def private_console(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/console/assets/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.startswith(
            ("/console/data/", "/console/auth/", "/console/manage/")
        ) or request.cookies.get("__Host-chatmoney-session"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers.add_vary_header("Authorization, Cookie")
        return response

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/console", status_code=307)

    @app.get("/accounts", include_in_schema=False)
    def accounts():
        return RedirectResponse("/console#members", status_code=307)

    @app.get("/console", include_in_schema=False)
    def console():
        return HTMLResponse(
            index,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            },
        )
