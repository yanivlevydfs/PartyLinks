"""Request filtering in front of the site and API: security headers, host check, scanner and
bad-bot blocking, per-IP rate limits and temporary bans.

Pure ASGI middleware. Settings are read from ``app.state.settings`` on every request, so admin
config changes apply without a restart. The client address is whatever uvicorn resolved: behind a
trusted proxy (``security.trusted_proxies``) that is the real visitor, not the proxy.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlsplit

from partylinks.admin import LOOPBACK
from partylinks.config import SecuritySettings

log = logging.getLogger(__name__)

# Paths only vulnerability scanners ask for: this site has no PHP, WordPress, dotfiles or Java.
PROBE = re.compile(
    r"\.(php\d?|asp|aspx|jsp|cgi|env|git|svn|htaccess|htpasswd|ini|bak|old|sql|swp|ds_store)(/|\?|$)"
    r"|/(wp-|wordpress|phpmyadmin|pma|cgi-bin|xmlrpc|actuator|server-status|boaform|vendor/phpunit"
    r"|\.well-known/security\.txt\.php|owa/|autodiscover|hnap1|solr|jenkins|manager/html|console/)"
    r"|\.\./|%2e%2e|/etc/passwd",
    re.IGNORECASE,
)
# Attack tools (not crawlers): refused and counted as a strike.
SCANNER_AGENTS = re.compile(
    r"sqlmap|nikto|nmap|masscan|zgrab|nuclei|wpscan|dirbuster|gobuster|feroxbuster|ffuf|acunetix|"
    r"netsparker|nessus|openvas|jaeles|whatweb|httpx-toolkit|censysinspect|l9explore|fuzz",
    re.IGNORECASE,
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# The listings as JSON: what a scraper wants, limited per IP beyond the general rate limit.
DATA_PATHS = ("/events", "/categories", "/posts", "/ticket-sites", "/locate")
# The app's own JSON (group plans, a user's account): only our pages call it, so script clients are
# refused there too. Not /plan/<id>: WhatsApp's link preview reads that page.
APP_API_PATHS = ("/plans", "/api/me")
# Card artwork and the site's own files: one home page draws well over a hundred, so they don't
# count toward the general rate limit (a few reloads banned a visitor).
STATIC_PATHS = ("/images/", "/artwork/", "/assets/", "/icons/", "/favicon")

CSP = "; ".join([  # noqa: FLY002 - one directive per line
    "default-src 'self'",
    # Google Analytics, after consent (consent.tsx); Cloudflare injects its cookieless Web Analytics beacon.
    "script-src 'self' https://www.googletagmanager.com https://static.cloudflareinsights.com",
    # Leaflet positions tiles with inline styles. Fonts are self-hosted (no Google Fonts: privacy).
    "style-src 'self' 'unsafe-inline'",
    # The accessibility widget fetches its language packs and dyslexia font from jsDelivr (URLs hardcoded).
    "font-src 'self' https://cdn.jsdelivr.net/npm/sienna-accessibility/",
    "img-src 'self' data: blob: https:",  # map tiles and event artwork hosted by the sources
    "worker-src 'self' blob:",  # the map's vector-tile workers
    "connect-src 'self' https://tiles.openfreemap.org https://cdn.jsdelivr.net/npm/sienna-accessibility/ https://*.google-analytics.com "
    "https://*.analytics.google.com https://*.googletagmanager.com https://cloudflareinsights.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])  # fmt: skip
HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), payment=(), usb=(), geolocation=(self)"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
# Public listings may be reused briefly by browsers and a CDN in front (Cloudflare), so a burst of
# visitors hits the database once per window. Vite asset names carry a content hash: cache forever.
PUBLIC_API = ("/events", "/categories", "/changes", "/site", "/posts", "/ticket-sites")
MAX_TRACKED = 50_000  # client addresses kept in each in-memory table before idle ones are dropped


# Cloudflare's edge (https://www.cloudflare.com/ips/). Behind its proxy, Railway's X-Real-IP is one
# of these; only then is CF-Connecting-IP the visitor (anyone can send that header straight to Railway).
# ponytail: fixed list, re-check cloudflare.com/ips if Cloudflare announces new ranges.
CLOUDFLARE = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)]  # fmt: skip


def _from_cloudflare(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in CLOUDFLARE)


def _forwarded_client(scope: dict[str, Any], cfg: SecuritySettings) -> None:
    """With ``trusted_proxies: ["*"]`` uvicorn takes the FIRST X-Forwarded-For entry, which the
    visitor writes. The LAST one is the address our own proxy saw: use it, for every layer below."""
    if cfg.trusted_proxies != ["*"]:
        return  # an explicit list: uvicorn already picks the first untrusted address from the right
    # Railway's edge puts the visitor in X-Real-IP (overwriting any sent); its own hop (152.233.x)
    # is last in X-Forwarded-For, so without this every visitor shares a few addresses and bans.
    headers = dict(scope["headers"])
    real = headers.get(b"x-real-ip", b"").decode("latin-1").strip()
    if real and _from_cloudflare(real):
        real = headers.get(b"cf-connecting-ip", b"").decode("latin-1").strip() or real
    if real:
        scope["client"] = (real, 0)
        return
    values = [v.decode("latin-1") for k, v in scope["headers"] if k == b"x-forwarded-for"]
    last = ",".join(values).rsplit(",", 1)[-1].strip()
    if last:
        scope["client"] = (last, 0)


def _cache_policy(path: str) -> bytes | None:
    if path.startswith("/assets/"):
        return b"public, max-age=31536000, immutable"
    if path.startswith(PUBLIC_API):
        return b"public, max-age=30, stale-while-revalidate=60"
    return None


# What a person opening a page sees instead of bare JSON when blocked (403) or rate limited (429).
_BLOCKED_PAGE = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SPOTX</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#06040b;color:#eee;
font:16px/1.6 system-ui,sans-serif;text-align:center;padding:24px}}h1{{font-size:22px;margin:0 0 8px}}
p{{margin:4px 0;color:#bbb}}</style></head><body><main><h1>{he_title}</h1><p>{he_text}</p>
<p dir="ltr" lang="en">{en_text}</p></main></body></html>"""
_BLOCKED_TEXT = {
    403: ("הגישה לאתר נחסמה זמנית", "מהכתובת שלך הגיעו יותר מדי ניסיונות שנכשלו. הגישה תחזור אוטומטית תוך שעה.",
          "Access from your address is blocked for up to an hour after repeated failed attempts."),
    429: ("יותר מדי בקשות", "נסו שוב בעוד דקה.", "Too many requests. Try again in a minute."),
}


class Guard:
    """In-memory counters (one process). ponytail: per-process state, move to Redis if the API
    ever runs as several workers."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self.strikes: dict[str, deque[float]] = defaultdict(deque)
        self.banned: dict[str, float] = {}  # ip -> ban end (monotonic)
        # ip -> wrong admin tokens already counted (hashed): the admin page sends several requests
        # with one token, so a single typo must be one strike, not five (it banned the owner).
        self.bad_tokens: dict[str, set[str]] = defaultdict(set)

    def bad_token(self, ip: str, token: str, cfg: SecuritySettings, now: float) -> None:
        """A wrong admin token: a strike the first time this IP sends it."""
        digest = hashlib.sha256(token.encode()).hexdigest()[:16]
        if digest in self.bad_tokens[ip]:
            return
        if len(self.bad_tokens) > MAX_TRACKED:
            self.bad_tokens.clear()
        self.bad_tokens[ip].add(digest)
        self.strike(ip, cfg, "bad admin token", now)

    @staticmethod
    def _count(window: deque[float], now: float, seconds: float) -> int:
        while window and window[0] <= now - seconds:
            window.popleft()
        return len(window)

    def pardon(self, ip: str | None = None) -> list[str]:
        """Lift the ban and strikes of ``ip``, or of every address; the addresses that were banned."""
        lifted = list(self.banned) if ip is None else [ip] if ip in self.banned else []
        for table in (self.banned, self.strikes, self.bad_tokens):
            if ip is None:
                table.clear()
            else:
                table.pop(ip, None)
        for key in [k for k in self.hits if ip is None or k[0] == ip]:  # its request counts too: no instant 429
            del self.hits[key]
        return lifted

    def is_banned(self, ip: str, now: float) -> bool:
        until = self.banned.get(ip)
        if until is not None and until <= now:
            del self.banned[ip]
            return False
        return until is not None

    def strike(self, ip: str, cfg: SecuritySettings, reason: str, now: float) -> None:
        window = self.strikes[ip]
        window.append(now)
        if self._count(window, now, cfg.ban_minutes * 60) >= cfg.ban_after_strikes:
            self.banned[ip] = now + cfg.ban_minutes * 60
            self.strikes.pop(ip, None)
            self.bad_tokens.pop(ip, None)
            log.warning("banned %s for %g min (last strike: %s)", ip, cfg.ban_minutes, reason)
        if len(self.strikes) > MAX_TRACKED or len(self.banned) > MAX_TRACKED:  # many addresses: drop expired
            for key in [k for k, w in self.strikes.items() if not w or w[-1] <= now - cfg.ban_minutes * 60]:
                del self.strikes[key]
            for key in [k for k, until in self.banned.items() if until <= now]:
                del self.banned[key]

    def over_limit(self, ip: str, bucket: str, limit: int, now: float, seconds: float = 60) -> bool:
        window = self.hits[(ip, bucket)]
        if self._count(window, now, seconds) >= limit:
            return True
        window.append(now)
        if len(self.hits) > MAX_TRACKED:  # drop idle clients so memory stays bounded
            for key in [k for k, w in self.hits.items() if not w or w[-1] <= now - 3600]:
                del self.hits[key]
        return False


class SecurityMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.guard = Guard()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope["app"].state
        state.guard = self.guard  # the admin API lists and lifts bans (/admin/api/bans)
        cfg: SecuritySettings = state.settings.security
        if not cfg.enabled:
            return await self.app(scope, receive, send)

        _forwarded_client(scope, cfg)
        ip = scope["client"][0] if scope.get("client") else "unknown"
        local = ip in LOOPBACK
        path: str = scope["path"]
        raw = scope.get("raw_path", b"").decode("latin-1") + "?" + scope.get("query_string", b"").decode("latin-1")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        agent = headers.get("user-agent", "")
        admin = path.startswith("/admin/api")
        now = time.monotonic()

        async def reject(code: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
            body, kind = json.dumps({"detail": detail}).encode(), b"application/json"
            if code in _BLOCKED_TEXT and "text/html" in headers.get("accept", ""):  # a browser page: words, not JSON
                he_title, he_text, en_text = _BLOCKED_TEXT[code]
                body, kind = _BLOCKED_PAGE.format(he_title=he_title, he_text=he_text, en_text=en_text).encode(), b"text/html; charset=utf-8"
            await send({"type": "http.response.start", "status": code, "headers": [
                (b"content-type", kind), (b"content-length", str(len(body)).encode()),
                *HEADERS, *(extra or []),
            ]})  # fmt: skip
            await send({"type": "http.response.body", "body": body})

        if not local and self.guard.is_banned(ip, now):  # first, token or not: a ban also stops token guessing
            return await reject(403, "forbidden")
        # The owner's own scripts (the valid admin token) are not rate limited: checks that sent many
        # requests banned the owner for an hour. A wrong token still strikes (below), so guessing is not helped.
        token = state.settings.admin.token
        given = headers.get("authorization", "").removeprefix("Bearer ").strip()
        owner = token is not None and bool(given) and hmac.compare_digest(given.encode(), token.get_secret_value().encode())
        host = headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        if cfg.allowed_hosts and host not in LOOPBACK | {"testserver"} | {h.lower() for h in cfg.allowed_hosts}:
            return await reject(400, "unknown host")
        if host in {h.lower() for h in cfg.redirect_hosts} and path != "/health":
            base = scope["app"].state.settings.site.base_url.rstrip("/")
            if urlsplit(base).hostname not in (None, host):
                query = scope.get("query_string", b"").decode("latin-1")
                location = base + scope.get("raw_path", b"").decode("latin-1") + (f"?{query}" if query else "")
                await send({"type": "http.response.start", "status": 301, "headers": [
                    (b"location", location.encode("latin-1")), (b"content-length", b"0"), *HEADERS,
                ]})  # fmt: skip
                return await send({"type": "http.response.body", "body": b""})
        if PROBE.search(raw) or SCANNER_AGENTS.search(agent):
            if not local:
                self.guard.strike(ip, cfg, f"probe {path[:80]}", now)
            return await reject(404, "not found")
        # RFC 8058 one-click unsubscribe comes from mail providers, some of which send no User-Agent.
        if not agent and path not in ("/health", "/api/email/unsubscribe"):
            return await reject(403, "forbidden")
        # Local calls (the startup cache warm-up runs through httpx) and health probes pass.
        blocked = any(bot.lower() in agent.lower() for bot in cfg.blocked_bots)
        if blocked and not local and path not in ("/robots.txt", "/health"):
            return await reject(403, "forbidden")
        script = any(c.lower() in agent.lower() for c in cfg.blocked_clients)
        if script and not local and path.startswith((*DATA_PATHS, *APP_API_PATHS, "/go/")):
            return await reject(403, "forbidden")
        if not local and not owner and not path.startswith(STATIC_PATHS):
            limit = cfg.admin_rate_limit_per_minute if admin else cfg.rate_limit_per_minute
            if self.guard.over_limit(ip, "admin" if admin else "all", limit, now):
                self.guard.strike(ip, cfg, "rate limit", now)
                return await reject(429, "too many requests", [(b"retry-after", b"60")])
            # Ticket links (/go/...) are what a harvester wants: a person clicks a handful a minute. Over
            # the limit is a 429 to come back later, not a strike: search engines and AI crawlers follow
            # every event's link (offers.url) faster than that, and a ban shut them out of the whole site.
            if path.startswith("/go/") and self.guard.over_limit(ip, "go", cfg.go_rate_limit_per_minute, now):
                return await reject(429, "too many requests", [(b"retry-after", b"60")])
            if path.startswith(DATA_PATHS) and (
                self.guard.over_limit(ip, "data", cfg.data_rate_limit_per_minute, now)
                or self.guard.over_limit(ip, "data-hour", cfg.data_rate_limit_per_hour, now, 3600)
            ):
                return await reject(429, "too many requests", [(b"retry-after", b"60")])
        try:
            length = int(headers.get("content-length") or 0)
        except ValueError:
            return await reject(400, "bad content-length")
        if length > cfg.max_body_bytes:
            return await reject(413, "request too large")

        docs = path.startswith(DOCS_PATHS)  # Swagger UI needs inline scripts and a CDN
        https = scope.get("scheme") == "https"
        cache = _cache_policy(path) if scope["method"] in ("GET", "HEAD") else None

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                out = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                out += HEADERS
                if not docs:
                    out.append((b"content-security-policy", CSP.encode()))
                if https:
                    out.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if admin:
                    out.append((b"cache-control", b"no-store"))
                elif cache and message["status"] == 200 and not any(k.lower() == b"cache-control" for k, _ in out):
                    out.append((b"cache-control", cache))
                # A wrong admin token on any route: brute-force guard. Only when a token was sent: a signed-out
                # visitor's GET /api/me is a 401 too, and must not count against their address.
                if message["status"] == 401 and not local and headers.get("authorization"):
                    self.guard.bad_token(ip, headers.get("authorization", ""), cfg, time.monotonic())
                message = {**message, "headers": out}
            await send(message)

        return await self.app(scope, receive, send_with_headers)
