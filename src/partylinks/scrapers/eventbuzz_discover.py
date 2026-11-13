"""Eventbuzz events that its /events page does not list (a producer's link-in-bio, Instagram ads).

The site has no sitemap, catalogue or API; an event lives only at /lp/event/<slug>. Its links are
gathered from the Wayback Machine's index (every page it archived lately) and a DuckDuckGo
``site:`` search. Each one is read from its own page like a listed one, so a past event is dropped
there; nothing is stored about the links themselves.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from partylinks.scrapers.eventbuzz import EventbuzzSettings

_SLUG = re.compile(r"eventbuzz\.co\.il/lp/event/([A-Za-z0-9_\-]+)")


def slugs(text: str) -> list[str]:
    """The event slugs named anywhere in ``text`` (a CDX listing, a results page), once each, in order."""
    return list(dict.fromkeys(_SLUG.findall(text)))


def wayback_url(template: str, days: int, now: datetime | None = None) -> str:
    since = ((now or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y%m%d")
    return template.format(since=since)


def search_urls(template: str, query: str, pages: int, per_page: int = 30) -> list[str]:
    return [template.format(q=quote(query), offset=i * per_page) for i in range(pages)]


async def fetch_direct(url: str, timeout: float, user_agent: str, s: EventbuzzSettings) -> str:
    """An index or search page, straight from our own address: archive.org's index takes 20-60 s to answer
    and the rotating proxies cut it off every time (2026-11-12: a run spent 40 minutes on it, retrying each)."""
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True) as client:
        # archive.org answers 503 now and then under load: s.direct_attempts tries (3, 10 s and 20 s apart).
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(s.direct_attempts), wait=wait_exponential(min=s.direct_wait_min_seconds, max=s.direct_wait_max_seconds),
            retry=retry_if_exception_type(httpx.HTTPError), reraise=True,
        ):  # fmt: skip
            with attempt:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
    raise AssertionError("unreachable: the last try returns or raises")
