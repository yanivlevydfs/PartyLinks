"""Eventer's sitemap: every public event page, so events on no slider or producer profile are found too.

``/sitemap.xml`` indexes ``sitemap_1.xml`` ... ``sitemap_N.xml`` (~145,000 pages since 2018) in creation
order, newest last, with no dates. An event is put on sale weeks or months before it starts, so the
newest event pages hold the upcoming ones; the scraper reads them newest first and stops only after a long
run of pages with no current event (past ones are dropped once their details are read: the sitemap gives
no date to skip them earlier). Private (link-only) events are not in
the sitemap: robots.txt lists them as ~98,000 exact ``Disallow: /<linkName>`` lines, also in creation
order, so the newest ``private_newest`` of those are read too (the owner wants every event, private ones
included).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from partylinks.parsing import parse_iso

if TYPE_CHECKING:
    from partylinks.scrapers.base import BaseScraper

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def is_over(event: dict[str, Any], default_tz: str, now: datetime | None = None) -> bool:
    """Ended (no end: 3 hours after the start, as the service counts it). No start: not known to be over."""
    schedule = event.get("schedule") or {}
    tz = (event.get("location") or {}).get("timezone") or default_tz
    start, end = parse_iso(schedule.get("start"), tz), parse_iso(schedule.get("end"), tz)
    if start is None:
        return False
    return (end or start + timedelta(hours=3)) < (now or datetime.now(UTC))


def link_name(url: str) -> str | None:
    """https://www.eventer.co.il/piza or .../events/piza -> "piza"; service pages (two segments) -> None."""
    parts = [unquote(p) for p in urlsplit(url).path.split("/") if p]
    if len(parts) == 2 and parts[0] == "events":
        parts = parts[1:]
    return parts[0] if len(parts) == 1 else None


def disallowed(robots: str) -> list[str]:
    """Exact single-segment paths disallowed for every crawler (``User-agent: *``), in file order, once each."""
    names: dict[str, None] = {}
    for_all = False
    for line in robots.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            for_all = value == "*"
        elif for_all and key == "disallow" and (name := link_name("https://x" + value)) and "*" not in name:
            names[name] = None
    return list(names)


async def private_names(scraper: BaseScraper, base_url: str, robots_path: str) -> list[str]:
    """Every robots-listed private event name, newest first."""
    return disallowed(await scraper.get_text(base_url + robots_path))[::-1]


async def public_names(scraper: BaseScraper, base_url: str, index_path: str, skip: set[str]) -> AsyncIterator[str]:
    """Every public event name of the sitemap, newest first, once each: its files are read from the newest
    back only as far as the caller keeps asking (the scraper stops where no current event is left)."""
    files = _LOC.findall(await scraper.get_text(base_url + index_path))
    seen: set[str] = set()
    for url in reversed(files):
        for loc in reversed(_LOC.findall(await scraper.get_text(url))):
            if (name := link_name(loc)) and name not in skip and name not in seen:
                seen.add(name)
                yield name
