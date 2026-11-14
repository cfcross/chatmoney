"""Browser identities resolve back to the existing tenant, product grants and roles."""

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import timedelta
from dataclasses import dataclass

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app import schema as s

from sqlalchemy.exc import IntegrityError
from contextlib import contextmanager
from app.db import engine, insert, lock, now, reject, scope, uid, update

COOKIE = "__Host-chatmoney-session"
SESSION_LIFE = timedelta(hours=8)
ACTIVATION_LIFE = timedelta(hours=24)
# A security-sensitive permission distinct from a salesperson's or a model-only admin's grant.
MANAGER_ROLES = frozenset({"config", "publish", "operator"})
HUMAN_ROLES = {
    "business",
    "seat",
    "observe",
    "config",
    "publish",
    "operator",
    "model_admin",
}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def workspace_context(session_id):
    # A tab can compare its workspace across account switches without seeing a login secret.
    return digest("workbench-context:" + session_id) if session_id else None


def check_context(request, session_id):
    tab = request.headers.get("x-workspace-context")
    if tab is not None and tab != workspace_context(session_id):
        reject(409, "WORKSPACE_CHANGED", "此标签页的登录账号已切换，请刷新后再操作。")


def username(value):
    value = unicodedata.normalize("NFKC", value).strip().lower()
    if not re.fullmatch(
        r"[a-z0-9\u3400-\u9fff][a-z0-9._@\-\u3400-\u9fff]{1,63}", value
    ):
        reject(
            422,
            "BAD_USERNAME",
            "账号需为 2 至 64 位中文、字母或数字，也可含点、短横线、下划线和 @。",
        )
    return value


def hash_password(password):
    return Argon2id(
        salt=secrets.token_bytes(16),
        length=32,
        iterations=2,
        lanes=1,
        memory_cost=19456,
    ).derive_phc_encoded(password.encode())


# Unknown accounts incur the same password verification work.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(encoded, supplied):
    try:
        Argon2id.verify_phc_encoded(supplied.encode(), encoded)
        return True
    except (InvalidKey, ValueError):
        return False


def can_manage(roles):
    return MANAGER_ROLES.issubset(roles)


def ordinary(roles):
    return set(roles) in ({"business"}, {"business", "own_agents"})


@contextmanager
def unique_login():
    try:
        yield
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        reject(409, "ACCOUNT_EXISTS", "此登录名已被使用，请换一个。")


def can_issue_members(c, who):
    if can_manage(who.roles):
        return True
    if not ordinary(who.roles):
        return False
    return bool(
        c.scalar(
            select(s.web_accounts.c.can_issue_accounts).where(
                s.web_accounts.c.tenant == who.tenant, s.web_accounts.c.id == who.actor
            )
        )
    )


def browser_transport(request):
    if request.scope["scheme"] != "https" or "x-forwarded-proto" in request.headers:
        reject(403, "HTTPS_REQUIRED", "请打开 HTTPS 工作台登录。")


def same_origin(request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if (
        origin != str(request.base_url).rstrip("/")
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        reject(403, "ORIGIN_DENIED", "请从当前工作台页面重新操作。")


def limit_attempt(name, address):
    # Reserve before doing expensive password verification. Commit even when login fails.
    moment = now()
    until = moment + timedelta(minutes=10)
    with engine().begin() as c:
        c.execute(s.login_limits.delete().where(s.login_limits.c.until <= moment))
        for label, value, cap in [("ip", address, 100), ("account", name, 10)]:
            bucket = label + ":" + digest(value)
            stmt = pg_insert(s.login_limits).values(
                bucket=bucket, attempts=1, until=until
            )
            count = c.scalar(
                stmt.on_conflict_do_update(
                    index_elements=[s.login_limits.c.bucket],
                    set_={"attempts": s.login_limits.c.attempts + 1},
                ).returning(s.login_limits.c.attempts)
            )
            if count > cap:
                reject(429, "LOGIN_LIMIT", "尝试次数过多，请在十分钟后重试。")


def clear_limit(c, name):
    c.execute(
        s.login_limits.delete().where(
            s.login_limits.c.bucket == "account:" + digest(name)
        )
    )


def find_account(c, name):
    c.execute(text("SELECT set_config('app.login_name',:name,true)"), {"name": name})
    return (
        c.execute(select(s.web_accounts).where(s.web_accounts.c.username == name))
        .mappings()
        .first()
    )


def actor(c, tenant, actor_id):
    return (
        c.execute(
            select(s.identities).where(
                s.identities.c.tenant == tenant,
                s.identities.c.id == actor_id,
                s.identities.c.active,
            )
        )
        .mappings()
        .first()
    )


def eligible(identity):
    return (
        identity is not None
        and bool(HUMAN_ROLES.intersection(identity.roles))
        and "adapter" not in identity.roles
    )


def live_session(c, tenant, actor_id, session_id):
    return (
        c.scalar(
            select(s.web_sessions.c.id).where(
                s.web_sessions.c.tenant == tenant,
                s.web_sessions.c.actor == actor_id,
                s.web_sessions.c.id == session_id,
                s.web_sessions.c.expires > now(),
            )
        )
        is not None
    )


def require_current_login(c, who):
    if who.session_id and not c.scalar(
        select(s.web_sessions.c.id)
        .where(
            s.web_sessions.c.tenant == who.tenant,
            s.web_sessions.c.actor == who.actor,
            s.web_sessions.c.id == who.session_id,
            s.web_sessions.c.expires > now(),
        )
        .with_for_update(read=True)
    ):
        reject(401, "UNAUTHENTICATED", "登录已失效，请重新登录。")


def cookie_identity(c, raw):
    if not raw or len(raw) != 43:
        return None
    session_id = digest(raw)
    c.execute(text("SELECT set_config('app.web_session',:id,true)"), {"id": session_id})
    session = (
        c.execute(
            select(s.web_sessions).where(
                s.web_sessions.c.id == session_id, s.web_sessions.c.expires > now()
            )
        )
        .mappings()
        .first()
    )
    if session:
        scope(c, session.tenant)
        identity = actor(c, session.tenant, session.actor)
        if eligible(identity):
            return identity, session_id
    return None


def revoke_cookie(c, raw, *, request=None):
    found = cookie_identity(c, raw)
    if found:
        identity, sid = found
        if request is not None:
            check_context(request, sid)
        c.execute(
            s.web_sessions.delete().where(
                s.web_sessions.c.tenant == identity.tenant, s.web_sessions.c.id == sid
            )
        )


def revoke_account(c, tenant, actor_id):
    c.execute(
        s.web_sessions.delete().where(
            s.web_sessions.c.tenant == tenant, s.web_sessions.c.actor == actor_id
        )
    )


def record(c, tenant, by, operation, target):
    insert(
        c,
        s.audit,
        tenant,
        uid("au"),
        actor=by,
        operation=operation,
        resource=target,
        detail={},
    )


@dataclass
class LoginResult:
    session: str | None = None
    change_password: bool = False


def check_password(password, name, old):
    if (
        len(password) < 12
        or not password.strip()
        or password.casefold() == name.casefold()
        or password == old
    ):
        reject(
            422, "PASSWORD_POLICY", "请设置至少 12 位的新密码，并与账号和初始密码区分。"
        )


def activate_or_login(
    name, supplied, setup_code, previous_cookie, *, replacement=None, new_username=None
):
    # Only a verified password or a one-time setup secret may create a web session.
    failure = (
        (401, "LOGIN_FAILED", "账号或密码不正确，或账号已停用。")
        if setup_code is None
        else (
            400,
            "SETUP_INVALID",
            "设置链接已失效或账号已停用，请联系管理员重新生成。",
        )
    )
    with engine().begin() as c:
        found = find_account(c, name)
        if found:
            scope(c, found.tenant)
            account = (
                c.execute(
                    select(s.web_accounts)
                    .where(
                        s.web_accounts.c.tenant == found.tenant,
                        s.web_accounts.c.id == found.id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            identity = (
                actor(c, account.tenant, account.id)
                if account and account.username == name
                else None
            )
        else:
            account = identity = None
        pending = bool(
            eligible(identity)
            and account.setup_hash
            and account.setup_until
            and account.setup_until > now()
        )
        # Initial passwords use a tagged digest. Historical invitation digests are plain SHA-256 hex.
        pending_password = pending and account.setup_hash.startswith("initial:")
        if setup_code is None:
            # Keep the normal login form for both initial and established passwords.
            ok = verify_password(
                account.password_hash
                if account and account.password_hash
                else DUMMY_HASH,
                supplied,
            )
            if pending_password and hmac.compare_digest(
                account.setup_hash, "initial:" + digest(supplied)
            ):
                if replacement is None:
                    clear_limit(c, name)
                    # This result never opens a session. The old secret must be presented again with the new password.
                    return LoginResult(change_password=True)
                check_password(replacement, new_username or name, supplied)
            elif (
                replacement is not None
                or not ok
                or not eligible(identity)
                or not account.password_hash
            ):
                reject(*failure)
        else:
            if (
                not pending
                or pending_password
                or not hmac.compare_digest(account.setup_hash, digest(setup_code))
            ):
                reject(*failure)
            replacement = supplied
            check_password(replacement, new_username or name, setup_code)
        if replacement is not None:
            update(
                c,
                s.web_accounts,
                identity.tenant,
                identity.id,
                username=new_username or name,
                password_hash=hash_password(replacement),
                setup_hash=None,
                setup_until=None,
            )
            revoke_account(c, identity.tenant, identity.id)
        # A deliberate account switch also invalidates this browser's previous server session.
        revoke_cookie(c, previous_cookie)
        scope(c, identity.tenant)
        c.execute(
            s.web_sessions.delete().where(
                s.web_sessions.c.tenant == identity.tenant,
                s.web_sessions.c.expires <= now(),
            )
        )
        raw = secrets.token_urlsafe(32)
        insert(
            c,
            s.web_sessions,
            identity.tenant,
            digest(raw),
            actor=identity.id,
            expires=now() + SESSION_LIFE,
        )
        clear_limit(c, name)
        record(
            c,
            identity.tenant,
            identity.id,
            "web.activate" if replacement is not None else "web.login",
            identity.id,
        )
        return LoginResult(session=raw)


def issue_setup(c, tenant, actor_id, name, *, by, temporary_password=False):
    """Issue one expiring secret, also used by the trusted local bootstrap."""
    code = secrets.token_urlsafe(15 if temporary_password else 32)
    stored_hash = ("initial:" if temporary_password else "") + digest(code)
    expires = now() + ACTIVATION_LIFE
    current = (
        c.execute(
            select(s.web_accounts)
            .where(s.web_accounts.c.tenant == tenant, s.web_accounts.c.id == actor_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if current:
        if current.username != name:
            reject(409, "ACCOUNT_EXISTS", "该身份已绑定其他登录账号。")
        update(
            c,
            s.web_accounts,
            tenant,
            actor_id,
            password_hash=None,
            setup_hash=stored_hash,
            setup_until=expires,
        )
        revoke_account(c, tenant, actor_id)
    else:
        insert(
            c,
            s.web_accounts,
            tenant,
            actor_id,
            username=name,
            setup_hash=stored_hash,
            setup_until=expires,
        )
    clear_limit(c, name)
    record(c, tenant, by, "web.issue_setup", actor_id)
    secret_field = "initial_password" if temporary_password else "setup_code"
    return {"username": name, secret_field: code, "expires": expires.isoformat()}


def existing_members(c, who):
    a, w = s.identities, s.web_accounts
    rows = c.execute(
        select(
            a.c.id,
            a.c.roles,
            a.c.products,
            w.c.username,
            w.c.password_hash.is_not(None).label("ready"),
            w.c.setup_until,
            w.c.can_issue_accounts,
        )
        .select_from(a.outerjoin(w, (w.c.tenant == a.c.tenant) & (w.c.id == a.c.id)))
        .where(a.c.tenant == who.tenant, a.c.active)
        .order_by(w.c.username.nulls_last(), a.c.id)
    ).mappings()
    return [
        dict(
            id=r.id,
            username=r.username,
            administrator=can_manage(set(r.roles)),
            issuer=bool(r.can_issue_accounts),
            deletable=can_manage(who.roles)
            and r.username is not None
            and ordinary(r.roles)
            and r.id != who.actor
            and set(r.products) <= who.products,
            delegatable=can_manage(who.roles)
            and r.username is not None
            and r.id != who.actor
            and ordinary(r.roles)
            and set(r.products) <= who.products,
            state="active"
            if r.ready
            else "invited"
            if r.setup_until and r.setup_until > now()
            else "unconfigured",
            manageable=r.id != who.actor
            and not can_manage(set(r.roles))
            and (
                can_manage(who.roles)
                or (ordinary(r.roles) and not r.can_issue_accounts)
            )
            and set(r.products) <= who.products
            and (set(r.roles) - {"business", "own_agents"}) <= who.roles,
        )
        for r in rows
        if HUMAN_ROLES.intersection(r.roles) and "adapter" not in r.roles
    ]


def issue_member(c, who, name, existing_id, *, temporary_password=False, password=None):
    # The caller provides neither the tenant nor role grants.
    lock(c, "web-members", who.tenant)
    require_current_login(c, who)
    if not can_issue_members(c, who):
        reject(403, "FORBIDDEN", "当前账号没有本企业的开通账号权限。")
    if existing_id:
        target = actor(c, who.tenant, existing_id)
        if not eligible(target) or existing_id == who.actor:
            reject(403, "MEMBER_DENIED", "请选择本企业其他可管理的成员。")
        if (
            not set(target.products) <= who.products
            or not (set(target.roles) - {"business", "own_agents"}) <= who.roles
        ):
            reject(403, "MEMBER_DENIED", "不能管理超出自己权限范围的身份。")
        if not can_manage(who.roles):
            delegated = c.scalar(
                select(s.web_accounts.c.can_issue_accounts).where(
                    s.web_accounts.c.tenant == who.tenant,
                    s.web_accounts.c.id == existing_id,
                )
            )
            if not ordinary(target.roles) or delegated:
                reject(403, "MEMBER_DENIED", "只能为本企业的普通成员分配账号密码。")
        # Admin resets for peer administrators require offline maintenance.
        if can_manage(set(target.roles)):
            reject(403, "MEMBER_DENIED", "管理员账号请由本机维护者恢复。")
    else:
        existing_id = uid("member")
        # No bearer credential is ever issued for this interactive-only identity.
        insert(
            c,
            s.identities,
            who.tenant,
            existing_id,
            roles=["business", "own_agents"],
            products=sorted(who.products),
            token_hash=digest(secrets.token_urlsafe(32)),
        )
    if password is not None:
        check_password(password, name, None)
        account = (
            select(s.web_accounts)
            .where(
                s.web_accounts.c.tenant == who.tenant,
                s.web_accounts.c.id == existing_id,
            )
            .with_for_update()
        )
        current = c.execute(account).mappings().first()
        fields = dict(
            username=name,
            password_hash=hash_password(password),
            setup_hash=None,
            setup_until=None,
        )
        if current:
            update(c, s.web_accounts, who.tenant, existing_id, **fields)
            revoke_account(c, who.tenant, existing_id)
        else:
            insert(c, s.web_accounts, who.tenant, existing_id, **fields)
        clear_limit(c, name)
        record(c, who.tenant, who.actor, "web.set_member_credentials", existing_id)
        return {"username": name}
    return issue_setup(
        c,
        who.tenant,
        existing_id,
        name,
        by=who.actor,
        temporary_password=temporary_password,
    )


def set_issuer(c, who, actor_id, enabled):
    # The same tenant lock serializes a grant/revocation against new issuances.
    lock(c, "web-members", who.tenant)
    require_current_login(c, who)
    if not can_manage(who.roles):
        reject(403, "FORBIDDEN", "只有本企业管理员可以授予开通账号权限。")
    target = actor(c, who.tenant, actor_id)
    account = c.scalar(
        select(s.web_accounts.c.id).where(
            s.web_accounts.c.tenant == who.tenant, s.web_accounts.c.id == actor_id
        )
    )
    if (
        account is None
        or not eligible(target)
        or not ordinary(target.roles)
        or not set(target.products) <= who.products
        or actor_id == who.actor
    ):
        reject(403, "MEMBER_DENIED", "请选择本企业已开通且在你管理范围内的普通成员。")
    update(c, s.web_accounts, who.tenant, actor_id, can_issue_accounts=enabled)
    record(
        c,
        who.tenant,
        who.actor,
        "web.grant_issuer" if enabled else "web.revoke_issuer",
        actor_id,
    )
    return {"id": actor_id, "can_issue": enabled}


def edit_credentials(c, who, target, name, password, current=None):
    lock(c, "web-members", who.tenant)
    require_current_login(c, who)
    self_edit = target == who.actor
    if self_edit:
        if current is None or not who.session_id:
            reject(403, "FORBIDDEN", "请使用本人的账号登录。")
    elif not can_issue_members(c, who) or not any(
        row["id"] == target and row["manageable"] for row in existing_members(c, who)
    ):
        reject(403, "MEMBER_DENIED", "你没有修改该成员账号的权限。")
    account = (
        c.execute(
            select(s.web_accounts)
            .where(s.web_accounts.c.tenant == who.tenant, s.web_accounts.c.id == target)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not account:
        reject(404, "NOT_FOUND", "此成员尚未开通登录账号。")
    if self_edit and (
        not account.password_hash or not verify_password(account.password_hash, current)
    ):
        reject(400, "WRONG_PASSWORD", "当前密码不正确，请重新输入。")
    name = username(name) if name is not None else account.username
    if name == account.username and password is None:
        reject(422, "NO_CHANGE", "请修改账号或填写新密码。")
    if not account.password_hash and password is None:
        reject(422, "PASSWORD_REQUIRED", "这个账号尚未设密，修改时请同时填写密码。")
    fields = dict(username=name, setup_hash=None, setup_until=None)
    if password is not None:
        check_password(password, name, current)
        fields["password_hash"] = hash_password(password)
    update(c, s.web_accounts, who.tenant, target, **fields)
    revoke_account(c, who.tenant, target)
    clear_limit(c, name)
    record(c, who.tenant, who.actor, "web.edit_credentials", target)
    return {"username": name}


def delete_member(c, who, target):
    lock(c, "web-members", who.tenant)
    require_current_login(c, who)
    identity = (
        c.execute(
            select(s.identities).where(
                s.identities.c.tenant == who.tenant, s.identities.c.id == target
            )
        )
        .mappings()
        .first()
    )
    if (
        not can_manage(who.roles)
        or not eligible(identity)
        or target == who.actor
        or not ordinary(identity.roles)
        or not set(identity.products) <= who.products
    ):
        reject(403, "MEMBER_DENIED", "只有管理员可以删除管理范围内的普通成员账号。")
    account = c.execute(
        select(s.web_accounts.c.username)
        .where(s.web_accounts.c.tenant == who.tenant, s.web_accounts.c.id == target)
        .with_for_update()
    ).first()
    if identity.active and not account:
        reject(404, "NOT_FOUND", "没有找到该成员的工作台账号。")
    if account:
        revoke_account(c, who.tenant, target)
        c.execute(
            s.web_accounts.delete().where(
                s.web_accounts.c.tenant == who.tenant, s.web_accounts.c.id == target
            )
        )
        update(c, s.identities, who.tenant, target, active=False)
        clear_limit(c, account.username)
        record(c, who.tenant, who.actor, "web.delete_member_login", target)
    return {"id": target, "deleted": True}
