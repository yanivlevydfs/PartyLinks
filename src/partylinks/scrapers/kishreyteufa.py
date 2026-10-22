"""Kishrey Teufa (kishrey-teufa.co.il) - tickets to concerts and festivals abroad.

The travel agency's "sport & show" engine (shared with Ophir Tours and Mona Tours: same
event ids) embeds its whole catalogue in every engine page, as a JSON array in
``<textarea id="allEventsJsonLite">``: id, local date and time, city, country (ISO
code, "UK" for Britain), venue, genre (``tourment``), performers, the lowest ticket
price and its currency code. One request (``show_path``); music rows only
(``categoryid`` 2), sport is skipped. Event pages are session-bound (POST + redirect),
so ``url`` is the performer's public search page. robots.txt allows these paths.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_FEED = re.compile(r'<textarea[^>]*id="allEventsJsonLite"[^>]*>(.*?)</textarea>', re.DOTALL)
CURRENCIES: dict[Any, str] = {1: "USD", 2: "EUR", 3: "GBP", 4: "ILS"}  # keyed by the feed's raw (untyped) value


class KishreyTeufaSettings(SourceSettings):
    site_url: str
    show_path: str  # any engine page; the shows page is the lightest that carries the feed
    search_path: str  # public search page of one performer (the event's link)
    music_category_id: int = 2


class KishreyTeufaScraper(BaseScraper[KishreyTeufaSettings]):
    name = "kishreyteufa"
    settings_model = KishreyTeufaSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        rows = parse_feed(await self.get_text(s.site_url + s.show_path))
        music = [r for r in rows if r.get("categoryid") == s.music_category_id]
        self.log.info("feed has %d events, %d music", len(rows), len(music))
        for row in music:
            try:
                yield parse_row(row, s.site_url + s.search_path, self.timezone)
            except ParseError as exc:
                self.record_failure(f"event {row.get('id')}", exc)


def parse_feed(page_html: str) -> list[dict[str, Any]]:
    match = _FEED.search(page_html)
    if not match:
        raise ParseError("allEventsJsonLite not found")
    rows = json.loads(html.unescape(match.group(1)))
    if not isinstance(rows, list):
        raise ParseError("allEventsJsonLite is not a list")
    return [r for r in rows if isinstance(r, dict)]


def parse_row(row: dict[str, Any], search_url: str, timezone: str) -> ScrapedEvent:
    title = clean_str(row.get("event"))
    country = country_code(row.get("countryId") or row.get("country"))
    start_at = parse_iso(row.get("date"), local_zone(country, timezone, row.get("city")))
    if not title or not start_at:
        raise ParseError("event without name or date")
    performers = [p for p in row.get("performers") or [] if isinstance(p, dict)]
    artists = [n for p in performers if (n := clean_str(p.get("name")))]
    genre = clean_str(row.get("tourment"))
    price = to_decimal(row.get("price")) or None
    params = {"type": 2, "category": "show", "categoryid": "show", "tourment": artists[0] if artists else title}
    if performers and performers[0].get("id"):
        params["tourmentId"] = performers[0]["id"]
    return ScrapedEvent(
        source=KishreyTeufaScraper.name,
        source_event_id=str(row["id"]),
        url=f"{search_url}?{urlencode(params)}",
        title=title,
        start_at=start_at,
        venue_name=clean_str(row.get("venue")),
        city=clean_str(row.get("city")),
        country=country,
        source_genres=[genre] if genre else [],
        artists=artists,
        currency=CURRENCIES.get(row.get("currency"), "EUR"),
        min_price=price,
        is_sold_out=row.get("tickets") == 0,
        extra={"tickets": row.get("tickets")},
    )
