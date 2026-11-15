"""Bounded business fields, not a web-to-prompt pipe. No full page, scripts or personal contact harvesting."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from app.research.network import Denied, clean_url

PRIVATE = re.compile(
    r"[\w.+-]+@[\w.-]+\.\w+|(?:\+?\d[\d ()-]{6,}\d)|(?:联系人|联系人姓名|个人电话|身份证|董事长|CEO\s*:|founded by|contact person)",
    re.I,
)
COMMAND = re.compile(
    r"ignore (?:all |the |previous |prior )*(?:instructions|rules)|system prompt|api[_ -]?key|忽略.{0,12}(指令|规则)|服务器配置|access token|password",
    re.I,
)


def safe(value, limit=200):
    if not isinstance(value, str):
        return ""
    v = " ".join(value.split())
    if (
        PRIVATE.search(v)
        or COMMAND.search(v)
        or re.search(r"[<>{}]|https?://", v, re.I)
    ):
        return ""
    return v[:limit]


TAGS = {"p", "h1", "h2", "h3", "title", "li", "dt", "dd", "a"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.items = []
        self.data = []
        self.meta = {}
        self.blocked = False
        self.follow = True

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): v or "" for k, v in attrs}
        identity = (d.get("id", "") + " " + d.get("class", "")).lower()
        if (tag == "input" and d.get("type", "").lower() == "password") or re.search(
            r"(?:^|\s)(?:cf-challenge|g-recaptcha|h-captcha|cf-turnstile)(?:\s|$)",
            identity,
        ):
            self.blocked = True
        if tag == "meta":
            k = (d.get("property") or d.get("name", "")).lower()
            if k in ("robots", "chatmoneyresearch") and re.search(
                r"noindex|noarchive|nosnippet|noai|none", d.get("content", ""), re.I
            ):
                self.blocked = True
            if k in ("robots", "chatmoneyresearch") and re.search(
                r"\bnofollow\b", d.get("content", ""), re.I
            ):
                self.follow = False
            if k in ("description", "og:description", "og:site_name"):
                self.meta[k] = safe(d.get("content", ""))
        if tag in (
            "input",
            "meta",
            "link",
            "img",
            "hr",
            "br",
            "source",
            "area",
            "base",
            "wbr",
            "embed",
            "param",
            "track",
            "col",
        ):
            return
        if len(self.stack) >= 96:
            raise Denied("TYPE")
        hidden = (
            (self.stack and self.stack[-1][2])
            or tag
            in (
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "style",
                "script",
                "template",
                "noscript",
                "iframe",
                "svg",
            )
            or "hidden" in d
            or d.get("aria-hidden") == "true"
        )
        ld = (
            tag == "script"
            and d.get("type", "").lower() == "application/ld+json"
            and not (self.stack and self.stack[-1][2])
        )
        self.stack.append(
            [
                tag,
                d.get("href", "")
                if tag == "a" and "nofollow" not in d.get("rel", "").lower().split()
                else None,
                bool(hidden),
                ld,
                "",
            ]
        )

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, s):
        for item in reversed(self.stack):
            if item[3]:
                if len(item[4]) < 24000:
                    item[4] += s[:24000]
                return
            if item[2]:
                return
            if item[0] in TAGS and len(item[4]) < 500:
                item[4] += s[:500]

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                closed = self.stack[i:]
                del self.stack[i:]
                for name, href, hidden, ld, txt in reversed(closed):
                    if ld and len(self.data) < 8:
                        try:
                            self.data.append(json.loads(txt))
                        except (ValueError, RecursionError):
                            pass
                    if (
                        not hidden
                        and name in TAGS
                        and txt.strip()
                        and len(self.items) < 450
                    ):
                        self.items.append((name, safe(txt), href))
                return


def parse(body, url):
    p = Page()
    # Static UTF-8 first; support a declared mainland legacy encoding without executing a browser.
    encoding = (
        "gb18030"
        if re.search(rb'charset\s*=\s*["\']?(?:gbk|gb2312|gb18030)', body[:3500], re.I)
        else "utf-8-sig"
    )
    try:
        p.feed(body.decode(encoding, errors="strict"))
        p.close()
    except (UnicodeError, ValueError, RecursionError, AssertionError):
        raise Denied("TYPE") from None
    if p.blocked or any(
        tag in ("title", "h1")
        and re.search(
            r"^(?:access denied|verify (?:you are|that you are) human|登录验证|安全验证|just a moment[. …]*)$",
            v,
            re.I,
        )
        for tag, v, _ in p.items
    ):
        raise Denied("RESTRICTED")
    facts = []
    links = []
    contact = []

    def put(k, v):
        v = safe(v)
        if v and (k, v) not in facts and len(facts) < 20:
            facts.append((k, v))

    for obj in p.data:
        nodes = obj if isinstance(obj, list) else [obj]
        for top in nodes[:12]:
            if not isinstance(top, dict):
                continue
            for x in [top] + (
                top["@graph"][:20] if isinstance(top.get("@graph"), list) else []
            ):
                if not isinstance(x, dict):
                    continue
                kinds = x.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if not isinstance(kinds, list):
                    continue
                if any(
                    k
                    in (
                        "Organization",
                        "Corporation",
                        "LocalBusiness",
                        "OnlineStore",
                        "Store",
                    )
                    for k in kinds
                ):
                    put("company_name", x.get("legalName") or x.get("name"))
                    put("business_description", x.get("description"))
                    put(
                        "business_type",
                        next(
                            k
                            for k in kinds
                            if k
                            in (
                                "Organization",
                                "Corporation",
                                "LocalBusiness",
                                "OnlineStore",
                                "Store",
                            )
                        ),
                    )
                    addr = x.get("address")
                    if isinstance(addr, dict):
                        country = addr.get("addressCountry")
                        put(
                            "country_or_region",
                            country.get("name")
                            if isinstance(country, dict)
                            else country,
                        )
                    put("service_market", x.get("areaServed"))
                if "Product" in kinds:
                    put("main_products", x.get("name"))
    put("site_name", p.meta.get("og:site_name"))
    put(
        "page_description",
        p.meta.get("og:description") or p.meta.get("description"),
    )
    labels = {
        "企业名称": "company_name",
        "公司名称": "company_name",
        "Company name": "company_name",
        "主营业务": "business_description",
        "主要产品": "main_products",
        "地区": "country_or_region",
        "服务市场": "service_market",
        "企业规模": "company_size",
        "需求信号": "demand_signal",
        "行业": "industry",
        "企业类型": "business_type",
    }
    for tag, v, href in p.items:
        if not v:
            continue
        if tag == "title":
            put("page_title", v)
        if tag == "h1":
            put("heading", v)
        if tag in ("p", "li", "dd", "h2"):
            m = re.fullmatch(r"(.{1,20}?)[：:]\s*(.+)", v)
            if m and m[1] in labels:
                put(labels[m[1]], m[2])
            elif tag == "p" and re.search(
                r"business|technology|developers|bookstore|software|我们|本公司|主营|公司|产品|研发|制造|our |we |speciali[sz]e|manufactur|suppli|services?",
                v,
                re.I,
            ):
                put("business_text", v)
        if tag == "a" and href and p.follow:
            if re.search(
                r"about|products?|services?|公司简介|关于我们|主营|主要产品|产品介绍",
                v,
                re.I,
            ):
                links.append(href)
            if re.search(
                r"contact us|business inquiries|partnerships|商务合作|联系我们|合作联系",
                v,
                re.I,
            ):
                try:
                    dest = clean_url(urljoin(url, href))
                    if urlsplit(dest).hostname == urlsplit(url).hostname:
                        contact.append(dest)
                except Denied:
                    pass
    return {
        "url": url,
        "facts": [{"field": k, "quote": v} for k, v in facts],
        "contact_candidates": list(dict.fromkeys(contact))[:2],
    }, links[:10]
