"""Tickchak LIVE's event sitemap: the events on no category page.

An event its seller left without a category (~320 of ~1,760 in Nov 2026: municipal culture halls,
museums, community centres) is on no category page the ``tickchak`` source lists. The event sitemap
(``/api/sitemap/event.xml``, redirected to static.tickchak.co.il) lists every event page with no
dates; each one not already listed is read once: its ``__NEXT_DATA__`` holds the event in the same
shape as a slider item. Past ones among them are dropped by the caller once their date is known.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx

from partylinks.parsing import ParseError, next_data

if TYPE_CHECKING:
    from partylinks.scrapers.base import BaseScraper

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def event_pages(xml: str) -> dict[str, str]:
    """Event code -> page URL for every ``.../event/<code>`` in the sitemap."""
    pages: dict[str, str] = {}
    for url in _LOC.findall(xml):
        parts = [unquote(p) for p in urlsplit(url).path.split("/") if p]
        if len(parts) >= 2 and parts[-2] == "event":
            pages.setdefault(parts[-1], url)
    return pages


def page_item(html: str, url: str) -> dict[str, Any]:
    """The event of an event page, as a slider item (its LIVE path set as ``url``), with
    ``categorySlug``: its main category page ("theater"; None when the seller set none we know)."""
    state = ((next_data(html).get("props") or {}).get("pageProps") or {}).get("initialState") or {}
    item = state.get("event")
    if not isinstance(item, dict) or not item.get("entityId") or not item.get("date"):
        raise ParseError("event missing")
    # The page lists the main categories by number; the event names its own by that number.
    slugs = {c.get("id"): c.get("friendlyUrl") for c in (state.get("page") or {}).get("mainCategories") or []}
    return {
        **item,
        "url": urlsplit(url).path,
        # The seller's page is pageUrl here (a slider item's link): it is the purchase link.
        "link": item.get("link") or item.get("pageUrl"),
        "categorySlug": slugs.get(item.get("mainCategory")),
    }


async def unlisted_items(scraper: BaseScraper[Any], sitemap_url: str, listed: set[Any]) -> list[dict[str, Any]]:
    """The sitemap's events not in ``listed``, one page request each; a failing page is recorded and skipped."""
    pages = event_pages(await scraper.get_text(sitemap_url))
    todo = [(code, url) for code, url in pages.items() if code not in listed]
    scraper.log.debug("sitemap: %d events, %d on no category page", len(pages), len(todo))

    async def read(page: tuple[str, str]) -> dict[str, Any] | None:
        try:
            html = await scraper.get_text(page[1])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # removed since the sitemap was built: redirects to /404
                return None
            raise
        return page_item(html, page[1])

    return [item async for item in scraper.stream_isolated(todo, read, lambda page: f"sitemap event {page[0]}")]
