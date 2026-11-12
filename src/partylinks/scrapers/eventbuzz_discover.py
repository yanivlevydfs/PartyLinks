"""Eventbuzz events that its /events page does not list (a producer's link-in-bio, Instagram ads).

The site has no sitemap, catalogue or API; an event lives only at /lp/event/<slug>. Its links are
gathered from the Wayback Machine's index (every page it archived lately) and a DuckDuckGo
``site:`` search. Each one is read from its own page like a listed one, so a past event is dropped
there; nothing is stored about the links themselves.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

_SLUG = re.compile(r"eventbuzz\.co\.il/lp/event/([A-Za-z0-9_\-]+)")


def slugs(text: str) -> list[str]:
    """The event slugs named anywhere in ``text`` (a CDX listing, a results page), once each, in order."""
    return list(dict.fromkeys(_SLUG.findall(text)))


def wayback_url(template: str, days: int, now: datetime | None = None) -> str:
    since = ((now or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y%m%d")
    return template.format(since=since)


def search_urls(template: str, query: str, pages: int, per_page: int = 30) -> list[str]:
    return [template.format(q=quote(query), offset=i * per_page) for i in range(pages)]
