"""Single-purpose HTTPS reader. No proxies, credentials, browser, cookies, script execution or automatic redirects."""

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

BOT = "ChatMoneyResearch"
HEADERS = {
    "User-Agent": BOT + "/1.4 (+https://agent.heiling.tech/console)",
    "Accept": "text/html,text/plain",
    "Accept-Encoding": "identity",
}
MAX_BYTES = 512_000
TOOL = "company.profile_read"
VERSION = "1.0"
META = dict(
    tool_name=TOOL,
    version=VERSION,
    provider="approved_company_website",
    capabilities=["organization_profile"],
    read_or_write="read",
    permission_scope="owner+published_revision+source",
    required_credentials=[],
    supported_operations=["read_public_business_pages"],
    rate_limit="serial per host, >=1s",
    timeout=22,
    audit_policy="URL, UTC, allowed excerpts, no page bodies",
)
ERR = {
    "URL": "仅支持已批准网站的标准 HTTPS 业务页面；不接受账号、查询参数或特殊地址。",
    "SOURCE": "该网址不在已批准的数据来源或路径内。",
    "ADDRESS": "目标不是允许访问的公网网站，未发起网页请求。",
    "ROBOTS": "该网站不允许当前自动读取方式，已停止访问。",
    "RESTRICTED": "网站限制自动读取或需要登录，已停止访问。",
    "NETWORK": "未能安全连接网站，请核实域名、证书和网络。",
    "TIME": "达到时间或请求预算，未继续读取。",
    "LARGE": "页面过大或使用了不支持的压缩方式。",
    "TYPE": "页面不是支持的静态网页，未执行脚本或下载附件。",
    "EMPTY": "未读到可保留的企业业务资料，请核实页面。",
    "SCOPE_CHANGED": "工具、资料或来源授权已变化，已停止研究。",
    "BUSY": "该网站暂有其他研究或处在访问间隔内，请稍后重试。",
    "INTERRUPTED": "上次执行中断；不会自动重做可能产生费用的调用。",
}


class Denied(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(ERR.get(code, "研究未完成。"))


def clean_url(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 1600
            or re.search(r"[\x00-\x20\x7f\\]", value)
        ):
            raise ValueError
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.username is not None
            or u.password is not None
            or u.port not in (None, 443)
            or u.query
            or "?" in value
            or u.fragment
        ):
            raise ValueError
        host = (u.hostname or "").encode("idna").decode("ascii").lower()
        if not re.fullmatch(
            r"(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}",
            host,
        ):
            raise ValueError
        if host.endswith(
            (
                ".localhost",
                ".local",
                ".internal",
                ".home.arpa",
                ".test",
                ".invalid",
                ".onion",
            )
        ):
            raise ValueError
        if any(
            x in host.split(".")
            for x in (
                "1688",
                "alibaba",
                "aliexpress",
                "amazon",
                "xiaohongshu",
                "taobao",
                "tmall",
                "amazonaws",
                "aliyuncs",
            )
        ):
            raise Denied("SOURCE")
        raw = u.path or "/"
        if re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c)", raw, re.I):
            raise ValueError
        path = unquote(raw, errors="strict")
        if (
            "//" in path
            or any(x in (".", "..") for x in path.split("/"))
            or re.search(r"[%?#\\:;@\x00-\x20\x7f]", path)
        ):
            raise ValueError
        if re.search(
            r"(?:^|/)(?:login|signin|admin|account|checkout|cart|logout|wp-admin)(?:/|$)",
            path,
            re.I,
        ):
            raise Denied("RESTRICTED")
        return "https://" + host + quote(path, safe="/-._~")
    except (ValueError, UnicodeError):
        raise Denied("URL") from None


def within(url, base):
    u, b = urlsplit(url), urlsplit(base)
    return u.netloc == b.netloc and (
        u.path == b.path or (b.path.endswith("/") and u.path.startswith(b.path))
    )


def public_ip(raw):
    try:
        x = ipaddress.ip_address(raw)
        if (
            "%" in raw
            or not x.is_global
            or x.is_multicast
            or x.is_reserved
            or x.is_loopback
            or x.is_link_local
        ):
            return False
        if x.version == 6:
            return (
                x in ipaddress.ip_network("2000::/3")
                and not x.sixtofour
                and not x.teredo
                and x not in ipaddress.ip_network("2001::/23")
                and x not in ipaddress.ip_network("3fff::/20")
            )
        return x not in ipaddress.ip_network("192.0.0.0/24")
    except ValueError:
        return False


@dataclass
class Reply:
    status: int
    headers: dict
    body: bytes


async def resolve(host):
    results = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(x[4][0] for x in results))


async def pinned_request(url, address):
    # TCP sees a validated numeric address only; Host, TLS SNI and certificate validation still use the approved hostname.
    u = httpx.URL(url)
    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=httpx.Timeout(5, connect=4)
    ) as c:
        async with c.stream(
            "GET",
            u.copy_with(host=address),
            headers={**HEADERS, "Host": u.host},
            extensions={"sni_hostname": u.host},
        ) as r:
            stream = r.extensions.get("network_stream")
            peer = stream.get_extra_info("server_addr") if stream else None
            if not peer or ipaddress.ip_address(peer[0]) != ipaddress.ip_address(
                address
            ):
                raise Denied("ADDRESS")
            h = {
                k: r.headers[k]
                for k in (
                    "content-type",
                    "location",
                    "content-length",
                    "content-encoding",
                    "x-robots-tag",
                )
                if k in r.headers
            }
            if r.status_code != 200:
                return Reply(r.status_code, h, b"")
            if (
                h.get("content-encoding", "identity").lower() not in ("", "identity")
                or int(h.get("content-length", 0)) > MAX_BYTES
            ):
                raise Denied("LARGE")
            buf = bytearray()
            async for chunk in r.aiter_raw():
                if len(buf) + len(chunk) > MAX_BYTES:
                    raise Denied("LARGE")
                buf.extend(chunk)
            return Reply(r.status_code, h, bytes(buf))


class Robots:
    def __init__(self, body):
        groups = []
        agents = []
        rules = []
        started = False
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = (s.strip() for s in line.split(":", 1))
            k = k.lower()
            if k == "user-agent":
                if started:
                    groups.append((agents, rules))
                    agents = []
                    rules = []
                    started = False
                agents.append(v.lower())
            elif agents:
                rules.append((k, v))
                started = True
        groups.append((agents, rules))
        # Apply the specific group(s); conservatively also respect wildcard restrictions.
        broad = [r for agents, rr in groups if "*" in agents for r in rr]
        specific = [
            r
            for agents, rr in groups
            if any(x and x != "*" and x in BOT.lower() for x in agents)
            for r in rr
        ]
        self.policies = [broad, specific]
        self.rules = broad + specific
        self.delay = 1.0
        for k, v in self.rules:
            if k == "crawl-delay":
                try:
                    n = float(v)
                    if not 0 <= n <= 10:
                        raise ValueError
                    self.delay = max(self.delay, n)
                except ValueError:
                    raise Denied("ROBOTS")
            if k in ("request-rate", "visit-time"):
                raise Denied("ROBOTS")

    def allowed(self, url):
        return all(self._allowed(urlsplit(url).path, group) for group in self.policies)

    @staticmethod
    def _allowed(p, rules):
        best = -1
        allow = True
        for k, v in rules:
            if k not in ("allow", "disallow") or not v:
                continue
            if len(v) > 512 or v.count("*") > 4:
                raise Denied("ROBOTS")
            end = v.endswith("$")
            v = v.removesuffix("$")
            # Glob matching without regex backtracking. Anchor at start, and at end only for '$'.
            pieces = v.split("*")

            def matches(path):
                if not path.startswith(pieces[0]):
                    return False
                if len(pieces) == 1:
                    return not end or path == v
                pos = len(pieces[0])
                for part in pieces[1:-1]:
                    idx = path.find(part, pos)
                    if idx < 0:
                        return False
                    pos = idx + len(part)
                tail = pieces[-1]
                return (
                    (path.endswith(tail) and len(path) - len(tail) >= pos)
                    if end
                    else tail in path[pos:]
                )

            # A denial expressed with raw UTF-8 must work for percent-encoded paths as well.
            if (matches(p) or matches(unquote(p))) and len(v.replace("*", "")) >= best:
                n = len(v.replace("*", ""))
                allow = (k == "allow") if n > best else allow and k == "allow"
                best = n
        return allow


async def crawl(
    start,
    scope,
    parse,
    *,
    max_pages=2,
    max_depth=1,
    request_budget=5,
    timeout=22,
    guard=lambda: None,
    audit=lambda status, size: None,
    dns=resolve,
    send=pinned_request,
    sleep=asyncio.sleep,
):
    start, scope = clean_url(start), clean_url(scope)
    if not within(start, scope):
        raise Denied("SOURCE")
    host = urlsplit(start).hostname
    n = 0
    last = None
    rules = Robots("")
    total = 0
    trace = []

    async def get(url):
        nonlocal n, last, total
        if n >= request_budget:
            raise Denied("TIME")
        if last is not None:
            await sleep(max(0, rules.delay - (time.monotonic() - last)))
        guard()
        ips = await dns(host)
        if not ips or not all(public_ip(ip) for ip in ips):
            raise Denied("ADDRESS")
        target = next((ip for ip in ips if ":" not in ip), ips[0])
        n += 1
        last = time.monotonic()
        resp = await send(url, target)
        total += len(resp.body)
        if total > 1_048_576 or len(resp.body) > MAX_BYTES:
            raise Denied("LARGE")
        audit(resp.status, len(resp.body))
        trace.append({"url": url, "http": resp.status, "bytes": len(resp.body)})
        return resp

    async def collect():
        nonlocal rules
        # Also leave a gap before starting a new job on this serialized host.
        await sleep(1.0)
        rr = await get("https://" + host + "/robots.txt")
        if rr.status == 200:
            if (
                len(rr.body) > 256_000
                or "html" in rr.headers.get("content-type", "")
                or b"<html" in rr.body[:400].lower()
            ):
                raise Denied("ROBOTS")
            rules = Robots(rr.body.decode("utf-8-sig", errors="strict"))
        elif rr.status not in (404, 410):
            raise Denied("ROBOTS")
        queue = [(start, 0)]
        seen = set()
        pages = []
        while queue and len(pages) < max_pages and n < request_budget:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            while True:
                if url in seen or not within(url, scope):
                    raise Denied("SOURCE")
                if not rules.allowed(url):
                    raise Denied("ROBOTS")
                seen.add(url)
                r = await get(url)
                if r.status in (301, 302, 303, 307, 308):
                    url = clean_url(urljoin(url, r.headers.get("location", "")))
                    continue
                if r.status != 200:
                    raise Denied(
                        "RESTRICTED" if r.status in (401, 403, 429, 451) else "NETWORK"
                    )
                break
            if r.headers.get("content-type", "").split(";")[0].strip().lower() not in (
                "text/html",
                "application/xhtml+xml",
            ):
                raise Denied("TYPE")
            if re.search(
                r"noindex|nosnippet|noarchive|noai|none",
                r.headers.get("x-robots-tag", ""),
                re.I,
            ):
                raise Denied("RESTRICTED")
            page, links = parse(r.body, url)
            if re.search(r"\bnofollow\b", r.headers.get("x-robots-tag", ""), re.I):
                links = []
                page["contact_candidates"] = []
            pages.append(page)
            if depth < max_depth:
                for item in links:
                    try:
                        dest = clean_url(urljoin(url, item))
                    except Denied:
                        continue
                    if (
                        within(dest, scope)
                        and dest not in seen
                        and rules.allowed(dest)
                        and len(queue) < 10
                    ):
                        queue.append((dest, depth + 1))
        return {"pages": pages, "requests": trace}

    try:
        async with asyncio.timeout(timeout):
            return await collect()
    except (TimeoutError, httpx.TimeoutException):
        raise Denied("TIME") from None
    except (httpx.HTTPError, OSError, ValueError, UnicodeError):
        raise Denied("NETWORK") from None
    finally:
        # Keep the per-host worker lock during any outstanding site-mandated cool down.
        if last is not None and rules.delay > 1:
            await sleep(max(0, rules.delay - (time.monotonic() - last)))
