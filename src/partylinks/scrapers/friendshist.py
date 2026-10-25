"""FRIENDS (friends-hist.co.il) - the Histadrut's culture club: shows, plays, stand-up and kids'
shows in halls all over the country, subsidised for Histadrut members.

The WordPress site has no shows in its REST API (no ``show`` post type there, no Events
Calendar, no event JSON-LD); tickets are sold on its Smarticket tenant
``tickets.friends-hist.co.il``. So two reads per run:

* ``GET <tickets host>/api/shows`` (~3 MB): every show with every performance, hall
  (``event_place``), price list and availability - parsed by the smarticket module.
  ``/event/<id>`` is the ticket page.
* The "all shows" page ``/כל-המופעים/`` (10 shows, ~1 MB) plus ``admin-ajax.php``
  ``action=load_more_shows_all&page=N`` for pages 2..``data-max-pages`` (~45). Each
  performance row has the city; bookable ones also hide a popup with the street address.
  This is the only source of city / address. The rows show only some of a show's
  performances and the ajax pages use other ids than the API, so rows are matched to API
  performances by (date, time, title), and a hall's city / address learnt from one matched
  row fills that hall's other performances.

robots.txt allows everything. The Smarticket tenant is also listed under ``smarticket``.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_int
from partylinks.scrapers import smarticket
from partylinks.scrapers.base import BaseScraper, SourceSettings, gather_or_cancel

type RowKey = tuple[str, str, str]  # (yyyy-mm-dd, HH:MM, normalised title)
_NOT_WORD = re.compile(r"\W+")


class FriendsHistSettings(SourceSettings):
    site_url: str
    listing_path: str
    ajax_path: str
    ajax_action: str
    max_pages: int = 80
    tickets_host: str
    shows_path: str
    event_page_path: str
    uploads_path: str
    # Organizer, and the venue of a performance with no hall.
    venue_name: str


class FriendsHistScraper(BaseScraper[FriendsHistSettings]):
    name = "friendshist"
    settings_model = FriendsHistSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        shows = smarticket.parse_shows_body(
            await self.get_text(f"https://{s.tickets_host}{s.shows_path}"), s.tickets_host
        )
        rows = await self.optional("listing", self._listing_rows()) or {}
        now = datetime.now(ZoneInfo(self.timezone))
        events = [
            e
            for e in build_events(shows, rows, s, self.timezone, self.record_failure)
            if (e.end_at or e.start_at or now) >= now
        ]
        self.log.info("%d upcoming events, %d with a city", len(events), sum(1 for e in events if e.city))
        for event in events:
            yield event

    async def _listing_rows(self) -> dict[RowKey, tuple[str | None, str | None]]:
        s = self.settings
        first = await self.get_text(s.site_url + s.listing_path)
        last = min(max_page(first), s.max_pages)

        async def page(n: int) -> str:
            data = {"action": s.ajax_action, "page": str(n)}
            return await self.optional(f"listing page {n}", self._post_text(data)) or ""

        pages = [first, *await gather_or_cancel(page(n) for n in range(2, last + 1))]
        rows: dict[RowKey, tuple[str | None, str | None]] = {}
        for body in pages:
            rows.update(parse_rows(body))
        self.log.info("listing: %d pages, %d performance rows", len(pages), len(rows))
        return rows

    async def _post_text(self, data: dict[str, str]) -> str:
        return (await self.request("POST", self.settings.site_url + self.settings.ajax_path, data=data)).text


def norm_title(title: str) -> str:
    return _NOT_WORD.sub("", html.unescape(title)).casefold()


def max_page(page_html: str) -> int:
    node = LexborHTMLParser(page_html).css_first("#load-more-shows")
    return to_int(node.attributes.get("data-max-pages")) or 1 if node else 1


def _address(text: str | None) -> str | None:
    """Drop the city the site appends twice: "street, city, city" -> "street, city"."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    return ", ".join(p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]) or None


def parse_rows(page_html: str) -> dict[RowKey, tuple[str | None, str | None]]:
    """Performance rows of a listing page: (date, time, title) -> (city, street address)."""
    tree = LexborHTMLParser(page_html)
    rows: dict[RowKey, tuple[str | None, str | None]] = {}
    for show in tree.css(".search_show_row"):
        heading = show.css_first(".wrap_title h3") or show.css_first("h3")
        if heading is None:
            continue
        title = norm_title(heading.text())
        for item in show.css(".events_list .wrap_item"):
            spans = [clean_str(n.text()) or "" for n in item.css(".wrap_event > span")]
            if len(spans) < 2:
                continue
            try:
                day = datetime.strptime(spans[-2], "%d.%m.%y").date().isoformat()
            except ValueError:
                continue
            city = item.css_first(".city span")
            booking = item.css_first("[data-event_id]")
            popup = booking and tree.css_first(
                f"#pop_content_{booking.attributes.get('data-event_id')} .address_wrap span"
            )
            rows[(day, spans[-1][:5], title)] = (
                clean_str(city.text()) if city else None,
                _address(popup.text()) if popup else None,
            )
    return rows


def build_events(
    shows: list[dict[str, Any]],
    rows: dict[RowKey, tuple[str | None, str | None]],
    s: FriendsHistSettings,
    timezone: str,
    on_error: Callable[[str, Exception], None] = lambda what, exc: None,
) -> list[ScrapedEvent]:
    """API performances as events, with city / address from the listing rows (by row, else by hall)."""
    tenant = smarticket.SmarticketTenant(host=s.tickets_host, venue_name=s.venue_name)
    st = smarticket.SmarticketSettings(
        cron=s.cron,
        tenant_domain=s.tickets_host,
        shows_path=s.shows_path,
        event_page_path=s.event_page_path,
        uploads_path=s.uploads_path,
        calendar_path="",
        tenants=[tenant],
    )
    found: list[tuple[ScrapedEvent, tuple[str | None, str | None] | None]] = []
    for show, performance in smarticket.iter_performances(shows):
        try:
            event = smarticket.parse_event(show, performance, tenant, st, timezone)
        except Exception as exc:  # noqa: BLE001 - one bad performance never drops the rest
            on_error(f"performance {performance.get('id')}", exc)
            continue
        if event is None or event.start_at is None:
            continue
        key = (event.start_at.date().isoformat(), event.start_at.strftime("%H:%M"), norm_title(event.title))
        found.append((event, rows.get(key) or rows.get(key[:2] + (norm_title(str(show.get("title") or "")),))))

    # A hall's city / address from any matched row of it.
    by_hall: dict[str, tuple[str | None, str | None]] = {}
    for event, row in found:
        hall = event.extra.get("hall")
        if row and hall:
            city, address = by_hall.get(hall, (None, None))
            by_hall[hall] = (city or row[0], address or row[1])

    events = []
    for event, row in found:
        hall_city, hall_address = by_hall.get(event.extra.get("hall") or "", (None, None))
        city, address = row or (None, None)
        events.append(
            event.model_copy(
                update={
                    "source": FriendsHistScraper.name,
                    "source_event_id": event.source_event_id.rsplit(":", 1)[-1],
                    "city": city or hall_city,
                    "address": address or hall_address,
                    "organizer": s.venue_name,
                    "extra": {k: v for k, v in event.extra.items() if k != "tenant"},
                }
            )
        )
    return events
