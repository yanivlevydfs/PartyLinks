"""Keeps a competitor from copying every event through the pages themselves (the JSON API has its own limits).

Every event has a server-rendered page with JSON-LD, and the sitemap lists them all: at the general 300 a
minute one address copied ~124k events in hours. So pages (any 200 text/html answer) are limited per IP
(security.page_rate_limit_per_minute / _per_day); a person loads few, since the app moves between pages
without the server. Search engines are let through, but only when they are who they say: the address's
reverse DNS is the engine's domain and that name resolves back to the address (a User-Agent is free to fake).

A trap (security.trap_path): a link no person sees, disallowed in robots.txt, nofollow. A client that fetches
it ignores the rules: its address is banned at once (security.ban_minutes).
"""

from __future__ import annotations

import logging
import re
import socket

import anyio
from cachetools import TTLCache

from partylinks.config import SecuritySettings

log = logging.getLogger(__name__)
# Crawlers that publish a reverse-DNS check (Google, Bing, Yandex, Apple, Baidu). Others (AI bots) get the limit.
CLAIMS = re.compile(
    r"googlebot|google-inspectiontool|googleother|bingbot|adidxbot|yandex|applebot|baiduspider", re.IGNORECASE
)
_verified: TTLCache[str, bool] = TTLCache(maxsize=20_000, ttl=86_400)  # ip -> checked, for a day


def _lookup(ip: str, domains: tuple[str, ...]) -> bool:
    try:
        host = socket.gethostbyaddr(ip)[0].lower().rstrip(".")
        if not any(host == d or host.endswith("." + d) for d in domains):
            return False
        return ip in {a[4][0] for a in socket.getaddrinfo(host, None)}
    except OSError:
        return False


async def real_crawler(ip: str, agent: str, cfg: SecuritySettings) -> bool:
    """``agent`` claims a search engine and ``ip`` really is one (DNS both ways, cached a day)."""
    if not CLAIMS.search(agent):
        return False
    if ip not in _verified:
        ok = await anyio.to_thread.run_sync(_lookup, ip, tuple(cfg.verified_crawler_domains))
        _verified[ip] = ok
        log.info("crawler check %s (%s): %s", ip, agent[:60], "verified" if ok else "NOT verified")
    return _verified[ip]


def pages_over(hits: dict, ip: str, cfg: SecuritySettings, now: float) -> bool:  # type: ignore[type-arg]
    """``ip`` has had its share of pages this minute or day (counted by ``count``, not here)."""
    for bucket, limit, seconds in (("page", cfg.page_rate_limit_per_minute, 60),
                                   ("page-day", cfg.page_rate_limit_per_day, 86_400)):  # fmt: skip
        window = hits.get((ip, bucket))
        while window and window[0] <= now - seconds:
            window.popleft()
        if window is not None and len(window) >= limit:
            return True
    return False


def count(hits: dict, ip: str, now: float) -> None:  # type: ignore[type-arg]
    """One page served to ``ip``."""
    hits[(ip, "page")].append(now)
    hits[(ip, "page-day")].append(now)
