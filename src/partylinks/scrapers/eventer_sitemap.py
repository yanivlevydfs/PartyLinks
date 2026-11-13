"""Eventer's sitemap: every public event page, so events on no slider or producer profile are found too.

``/sitemap.xml`` indexes ``sitemap_1.xml`` ... ``sitemap_N.xml`` (~145,000 pages since 2018) in creation
order, newest last, with no dates. An event is put on sale weeks or months before it starts, so the
newest ``newest`` event pages hold the upcoming ones; past ones among them are dropped once their
details are read (the sitemap gives no date to skip them earlier). Private (link-only) events are not in
the sitemap: robots.txt lists them as ~98,000 exact ``Disallow: /<linkName>`` lines, also in creation
order, so the newest ``private_newest`` of those are read too (the owner wants every event, private ones
included).
"""

from __future__ import annotations

import re
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


def newest_names(sitemaps: list[str], newest: int, skip: set[str]) -> list[str]:
    """Up to ``newest`` link names from the end of the sitemap files (given oldest first), newest first."""
    names: dict[str, None] = {}
    for xml in reversed(sitemaps):
        for url in reversed(_LOC.findall(xml)):
            if (name := link_name(url)) and name not in skip:
                names[name] = None
                if len(names) >= newest:
                    return list(names)
    return list(names)


async def sitemap_link_names(
    scraper: BaseScraper, base_url: str, index_path: str, robots_path: str, newest: int, private_newest: int = 0
) -> list[str]:
    """The newest ``private_newest`` robots-listed private names, newest first, then sitemap files read from
    the newest back until ``newest`` public event names are collected."""
    private = disallowed(await scraper.get_text(base_url + robots_path))
    names = private[::-1][:private_newest]
    skip = set(private)
    files = _LOC.findall(await scraper.get_text(base_url + index_path))
    read: list[str] = []
    for url in reversed(files):
        read.insert(0, await scraper.get_text(url))
        public = newest_names(read, newest, skip)
        if len(public) >= newest:
            return names + public
    return names + newest_names(read, newest, skip)
