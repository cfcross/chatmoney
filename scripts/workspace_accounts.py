"""Prepare private, one-time browser enrollment links for existing enterprise administrators."""

import argparse
import re
import hashlib
import hmac
from html import escape, unescape
from pathlib import Path
import os
import subprocess
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import create_engine, select
from app import schema as s, web_identity as web
from app.db import lock, now, scope
from app.team import TEAM, ADMIN, PRODUCT
from sqlalchemy.dialects.postgresql import insert as pg_insert
import secrets
import webbrowser


def write_private_page(path, link, name, company):
    # Opening the private file takes the administrator to the actual editable HTTPS form.
    html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="refresh" content="0; url={escape(link, quote=True)}"><title>设置工作台密码</title>
<style>body{{margin:12vh auto;padding:24px;max-width:440px;font:16px/1.7 system-ui;color:#193735}}
a{{display:inline-block;background:#116852;color:white;padding:8px 18px;border-radius:6px}}</style>
<h1>设置工作台密码</h1><p>{escape(company)} · 账号 <strong>{escape(name)}</strong></p>
<p>仅供此账号的管理员首次使用。链接在 24 小时内有效，设置密码后失效。</p>
<a href="{escape(link, quote=True)}" rel="noreferrer">打开网页设置密码</a>
<p>已经设置过密码时，可直接<a href="{escape(link.split("#")[0], quote=True)}">登录工作台</a>。</p></html>"""
    descriptor, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            if os.name == "nt":
                # Windows ignores POSIX file modes. Remove inherited grants before writing the link.
                current = subprocess.check_output(
                    ["whoami.exe", "/user", "/fo", "csv", "/nh"]
                )
                sid = re.search(rb"\bS-1-[0-9-]+\b", current)
                if not sid:
                    raise RuntimeError("Cannot identify the local administrator")
                subprocess.run(
                    [
                        "icacls.exe",
                        str(temp),
                        "/inheritance:r",
                        "/grant:r",
                        "*" + sid[0].decode("ascii") + ":F",
                        "*S-1-5-18:F",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            f.write(html)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def ensure_team(c):
    def put(table, **values):
        c.execute(pg_insert(table).values(**values).on_conflict_do_nothing())

    put(s.tenants, id=TEAM, name="ChatMoney 团队", company_ref=TEAM + "@1")
    put(
        s.products, tenant=TEAM, id=PRODUCT, name="通用产品", version_ref=PRODUCT + "@1"
    )
    put(
        s.identities,
        tenant=TEAM,
        id=ADMIN,
        roles=[
            "business",
            "config",
            "publish",
            "operator",
            "seat",
            "correct",
            "observe",
            "model_admin",
        ],
        products=[PRODUCT],
        token_hash=web.digest(secrets.token_urlsafe(32)),
    )


def prepare(base, output, reset=None, *, team_only=False):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("Use an HTTPS workbench address")
    output.mkdir(mode=0o700, exist_ok=True, parents=True)
    database = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    pending_page = None
    try:
        with database.begin() as c:
            # A database-owner process is needed for this offline setup, never the public API.
            if team_only:
                ensure_team(c)
            all_admins = (
                c.execute(
                    select(s.identities, s.tenants.c.name.label("company"))
                    .join(s.tenants, s.identities.c.tenant == s.tenants.c.id)
                    .where(s.identities.c.active)
                )
                .mappings()
                .all()
            )
            eligible = [
                i
                for i in all_admins
                if web.eligible(i) and web.can_manage(set(i.roles))
            ]
            if team_only:
                eligible = [i for i in eligible if (i.tenant, i.id) == (TEAM, ADMIN)]
            if reset:
                eligible = [i for i in eligible if (i.tenant, i.id) == tuple(reset)]
                if not eligible:
                    raise SystemExit("Active administrator not found")
            for identity in eligible:
                scope(c, identity.tenant)
                lock(c, "web-members", identity.tenant)
                query = select(s.web_accounts).where(
                    s.web_accounts.c.tenant == identity.tenant,
                    s.web_accounts.c.id == identity.id,
                )
                old = c.execute(query.with_for_update()).mappings().first()
                # File names never contain custom path characters or login secrets.
                suffix = hashlib.sha256(
                    (identity.tenant + "\0" + identity.id).encode()
                ).hexdigest()[:12]
                stem = (
                    identity.id
                    if identity.id in ("tob-admin", "toc-admin")
                    and identity.id == identity.tenant + "-admin"
                    else suffix
                )
                path = output / (
                    "开始使用ChatMoney.html"
                    if (identity.tenant, identity.id) == (TEAM, ADMIN)
                    else stem + ".html"
                )
                if not reset and old and old.password_hash:
                    continue
                if team_only:
                    pending_page = path
                if (
                    not reset
                    and old
                    and old.setup_until
                    and old.setup_until > now()
                    and path.is_file()
                ):
                    # Repeated startup should keep a still-valid private link usable.
                    m = re.search(
                        r'<a href="([^"]+#activate\?[^"]+)"',
                        path.read_text(encoding="utf8"),
                    )
                    if m:
                        uri = urlsplit(unescape(m[1]))
                        saved = parse_qs(uri.fragment.removeprefix("activate?")).get(
                            "code", [""]
                        )[0]
                        if (
                            uri.netloc == parsed.netloc
                            and uri.path == parsed.path
                            and old.setup_hash
                            and hmac.compare_digest(web.digest(saved), old.setup_hash)
                        ):
                            continue
                name = old.username if old else web.username(identity.id)
                result = web.issue_setup(
                    c, identity.tenant, identity.id, name, by="local-maintainer"
                )
                link = (
                    base
                    + "#activate?"
                    + urlencode({"account": name, "code": result["setup_code"]})
                )
                write_private_page(path, link, name, identity.company)
                print("Prepared private administrator setup page:", path.name)
    finally:
        database.dispose()
    return pending_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://localhost:8443/console")
    parser.add_argument("--out", type=Path, default=Path(".runtime/account-setup"))
    parser.add_argument(
        "--reset-admin",
        nargs=2,
        metavar=("TENANT", "ACTOR"),
        help="Invalidate the named administrator's password and sessions, and issue a new setup link",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open setup on first use, otherwise open the workbench",
    )
    args = parser.parse_args()
    pending = prepare(
        args.base, args.out, args.reset_admin, team_only=args.reset_admin is None
    )
    if args.open:
        if not webbrowser.open(pending.resolve().as_uri() if pending else args.base):
            raise SystemExit("Open the private setup page or the workbench in a browser.")


if __name__ == "__main__":
    main()
