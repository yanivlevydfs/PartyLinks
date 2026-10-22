"""Jerusalem Theatre (jerusalem-theatre.co.il) - theatre, music, dance, festivals and children's shows.

Its own box office, on no other ticket site. One request: the site's board feed
(``na_ajax.php?action=getBoard``) lists every upcoming show (``items``) with title, subtitle,
length, image, page link, genre (``parent-group``, titled in ``groups``), a free flag and
``dates[]``: one entry per performance with a unix time, hall id, ticket id and status. The
times are the Israeli wall clock written as UTC (a 20:00 show reads 20:00Z). Prices are only in
the box office, so none are given. robots.txt allows everything but the admin paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings


class JerusalemTheatreSettings(SourceSettings):
    site_url: str
    board_path: str
    venue_name: str
    address: str
    city: str
    # Genres (parent-group ids) left out, e.g. eventGrp_cinema (film screenings).
    skip_groups: list[str] = Field(default_factory=list)


class JerusalemTheatreScraper(BaseScraper[JerusalemTheatreSettings]):
    name = "jerusalemtheatre"
    settings_model = JerusalemTheatreSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        board = await self.get_json(s.site_url + s.board_path, params={"action": "getBoard"}, expect=dict)
        events = parse_board(board, s, self.timezone)
        self.log.info("listed %d performances", len(events))
        for event in events:
            yield event


def parse_board(board: dict[str, Any], s: JerusalemTheatreSettings, timezone: str) -> list[ScrapedEvent]:
    items = board.get("items")
    if not isinstance(items, dict):
        raise ParseError("board without items")
    groups = board.get("groups") if isinstance(board.get("groups"), dict) else {}
    events: dict[str, ScrapedEvent] = {}
    for show in items.values():
        if not isinstance(show, dict) or show.get("parent-group") in s.skip_groups:
            continue
        for performance in show.get("dates") or []:
            if isinstance(performance, dict) and (event := parse_performance(show, performance, groups, s, timezone)):
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
        extra={"show_id": clean_str(show.get("id")), "hall_id": clean_str(performance.get("hall"))},
    )
