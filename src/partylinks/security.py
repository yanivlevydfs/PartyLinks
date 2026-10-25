"""Request filtering in front of the site and API: security headers, host check, scanner and
bad-bot blocking, per-IP rate limits and temporary bans.

Pure ASGI middleware. Settings are read from ``app.state.settings`` on every request, so admin
config changes apply without a restart. The client address is whatever uvicorn resolved: behind a
trusted proxy (``security.trusted_proxies``) that is the real visitor, not the proxy.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any

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

CSP = "; ".join([  # noqa: FLY002 - one directive per line
    "default-src 'self'",
    "script-src 'self'",
    # Leaflet positions tiles with inline styles. Fonts are self-hosted (no Google Fonts: privacy).
    "style-src 'self' 'unsafe-inline'",
    # The accessibility widget fetches its language packs and dyslexia font from jsDelivr (URLs hardcoded).
    "font-src 'self' https://cdn.jsdelivr.net/npm/sienna-accessibility/",
    "img-src 'self' data: blob: https:",  # map tiles and event artwork hosted by the sources
    "connect-src 'self' https://cdn.jsdelivr.net/npm/sienna-accessibility/",
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


def _forwarded_client(scope: dict[str, Any], cfg: SecuritySettings) -> None:
    """With ``trusted_proxies: ["*"]`` uvicorn takes the FIRST X-Forwarded-For entry, which the
    visitor writes. The LAST one is the address our own proxy saw: use it, for every layer below."""
    if cfg.trusted_proxies != ["*"]:
        return  # an explicit list: uvicorn already picks the first untrusted address from the right
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


class Guard:
    """In-memory counters (one process). ponytail: per-process state, move to Redis if the API
    ever runs as several workers."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self.strikes: dict[str, deque[float]] = defaultdict(deque)
        self.banned: dict[str, float] = {}  # ip -> ban end (monotonic)

    @staticmethod
    def _count(window: deque[float], now: float, seconds: float) -> int:
        while window and window[0] <= now - seconds:
            window.popleft()
        return len(window)

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
            log.warning("banned %s for %g min (last strike: %s)", ip, cfg.ban_minutes, reason)
        if len(self.strikes) > MAX_TRACKED or len(self.banned) > MAX_TRACKED:  # many addresses: drop expired
            for key in [k for k, w in self.strikes.items() if not w or w[-1] <= now - cfg.ban_minutes * 60]:
                del self.strikes[key]
            for key in [k for k, until in self.banned.items() if until <= now]:
                del self.banned[key]

    def over_limit(self, ip: str, bucket: str, limit: int, now: float) -> bool:
        window = self.hits[(ip, bucket)]
        if self._count(window, now, 60) >= limit:
            return True
        window.append(now)
        if len(self.hits) > MAX_TRACKED:  # drop idle clients so memory stays bounded
            for key in [k for k, w in self.hits.items() if not w or w[-1] <= now - 60]:
                del self.hits[key]
        return False


class SecurityMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.guard = Guard()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cfg: SecuritySettings = scope["app"].state.settings.security
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
            body = json.dumps({"detail": detail}).encode()
            await send({"type": "http.response.start", "status": code, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                *HEADERS, *(extra or []),
            ]})  # fmt: skip
            await send({"type": "http.response.body", "body": body})

        if not local and self.guard.is_banned(ip, now):
            return await reject(403, "forbidden")
        host = headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        if cfg.allowed_hosts and host not in LOOPBACK | {"testserver"} | {h.lower() for h in cfg.allowed_hosts}:
            return await reject(400, "unknown host")
        if PROBE.search(raw) or SCANNER_AGENTS.search(agent):
            if not local:
                self.guard.strike(ip, cfg, f"probe {path[:80]}", now)
            return await reject(404, "not found")
        if not agent and path != "/health":
            return await reject(403, "forbidden")
        if any(bot.lower() in agent.lower() for bot in cfg.blocked_bots) and path != "/robots.txt":
            return await reject(403, "forbidden")
        if not local:
            limit = cfg.admin_rate_limit_per_minute if admin else cfg.rate_limit_per_minute
            if self.guard.over_limit(ip, "admin" if admin else "all", limit, now):
                self.guard.strike(ip, cfg, "rate limit", now)
                return await reject(429, "too many requests", [(b"retry-after", b"60")])
            # Ticket links (/go/...) are what a harvester wants: a person clicks a handful a minute.
            if path.startswith("/go/") and self.guard.over_limit(ip, "go", cfg.go_rate_limit_per_minute, now):
                self.guard.strike(ip, cfg, "ticket link rate limit", now)
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
                if message["status"] == 401 and not local:  # wrong admin token on any route: brute-force guard
                    self.guard.strike(ip, cfg, "bad admin token", time.monotonic())
                message = {**message, "headers": out}
            await send(message)

        return await self.app(scope, receive, send_with_headers)
