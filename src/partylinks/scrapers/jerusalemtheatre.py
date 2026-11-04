"""Jerusalem Theatre (jerusalem-theatre.co.il) - theatre, music, dance, festivals and children's shows.

Its own box office, on no other ticket site. One request: the site's board feed
(``na_ajax.php?action=getBoard``) lists every upcoming show (``items``) with title, subtitle,
length, image, page link, genre (``parent-group``, titled in ``groups``), a free flag and
``dates[]``: one entry per performance with a unix time, hall id, ticket id and status. The
times are the Israeli wall clock written as UTC (a 20:00 show reads 20:00Z). Prices: the box office
is a PRES host (the "buy" button opens ``jtheater.pres.global/order/<ticket id>``), whose
``/api/presentations/<ticket id>/ticketGroups`` gives each price level; one request per show, from
its first listed performance, applied to all of them (as the pres scraper does). robots.txt
allows everything but the admin paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.pres import parse_ticket_groups


class JerusalemTheatreSettings(SourceSettings):
    site_url: str
    board_path: str
    venue_name: str
    address: str
    city: str
    # Genres (parent-group ids) left out, e.g. eventGrp_cinema (film screenings).
    skip_groups: list[str] = Field(default_factory=list)
    # The PRES box office's price levels of one performance (``{id}``: the board's ticket id).
    ticket_groups_url: str = "https://jtheater.pres.global/api/presentations/{id}/ticketGroups"


class JerusalemTheatreScraper(BaseScraper[JerusalemTheatreSettings]):
    name = "jerusalemtheatre"
    settings_model = JerusalemTheatreSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        board = await self.get_json(s.site_url + s.board_path, params={"action": "getBoard"}, expect=dict)
        events = parse_board(board, s, self.timezone, self.record_failure)
        self.log.debug("listed %d performances", len(events))
        shows: dict[str, list[ScrapedEvent]] = {}
        for event in events:
            shows.setdefault(str(event.extra.get("show_id")), []).append(event)

        async def priced(show: list[ScrapedEvent]) -> list[ScrapedEvent]:
            # Free performances keep their price 0; the rest share the first paid one's levels.
            ticket = next((e.extra["ticket"] for e in show if e.min_price is None and e.extra.get("ticket")), None)
            if ticket:
                url = s.ticket_groups_url.format(id=ticket)
                payload = await self.optional(f"prices {ticket}", self.get_json(url, expect=dict))
                apply_prices(show, parse_ticket_groups(payload) if payload else [])
            return show

        async for show in self.stream_isolated(shows.values(), priced, label=lambda show: show[0].title):
            for event in show:
                yield event


def apply_prices(events: list[ScrapedEvent], tiers: list[TicketTier]) -> None:
    """The show's price levels on each of its (not free) performances."""
    prices = [t.price for t in tiers if t.price is not None]
    for event in events:
        if event.min_price is None and prices:
            event.tickets = [t.model_copy() for t in tiers]
            event.min_price, event.max_price = min(prices), max(prices)


def parse_board(
    board: dict[str, Any],
    s: JerusalemTheatreSettings,
    timezone: str,
    on_error: Callable[[str, Exception], None] = lambda what, exc: None,
) -> list[ScrapedEvent]:
    items = board.get("items")
    if not isinstance(items, dict):
        raise ParseError("board without items")
    groups = board.get("groups") if isinstance(board.get("groups"), dict) else {}
    events: dict[str, ScrapedEvent] = {}
    for show in items.values():
        if not isinstance(show, dict) or show.get("parent-group") in s.skip_groups:
            continue
        for performance in show.get("dates") or []:
            try:
                event = parse_performance(show, performance, groups, s, timezone) if isinstance(performance, dict) else None
            except Exception as exc:  # noqa: BLE001 - one bad performance never drops the board
                on_error(f"show {show.get('id')}", exc)
                continue
            if event:
                events.setdefault(event.source_event_id, event)
    return list(events.values())


def parse_performance(
    show: dict[str, Any], performance: dict[str, Any], groups: dict[str, Any], s: JerusalemTheatreSettings, timezone: str
) -> ScrapedEvent | None:
    stamp, title, link = to_int(performance.get("date")), clean_str(show.get("title")), clean_str(show.get("link"))
    if not stamp or not title or not link:
        return None
    # Wall-clock time written as UTC: keep the clock reading, set the Israeli zone.
    start = datetime.fromtimestamp(stamp, UTC).replace(tzinfo=ZoneInfo(timezone))
    genre = clean_str((groups.get(show.get("parent-group") or "") or {}).get("title"))
    free = bool(show.get("free")) or performance.get("status") == "free"
    image = clean_str(show.get("img"))
    if image and "/images/default/" in image:  # the site's placeholder, not the show's artwork
        image = None
    ticket = clean_str(performance.get("ticket"))
    details = [clean_str(show.get("subtitle")), clean_str(show.get("length")), clean_str(performance.get("remark") or show.get("remark"))]
    return ScrapedEvent(
        source=JerusalemTheatreScraper.name,
        source_event_id=ticket or f"{show.get('id')}:{stamp}",
        url=link,
        title=title,
        description="\n".join(d for d in details if d) or None,
        start_at=start,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        source_genres=[genre] if genre else [],
        images=[image] if image else [],
        tickets=[TicketTier(name="כניסה חופשית", price=0, is_free=True)] if free else [],
        min_price=0 if free else None,
        max_price=0 if free else None,
        extra={"show_id": clean_str(show.get("id")), "hall_id": clean_str(performance.get("hall")), "ticket": ticket},
    )
