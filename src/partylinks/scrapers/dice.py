"""DICE (dice.fm) - a ticketing app for club nights, DJ sets and gigs in Europe and the US.

Only the cities Israelis fly to most are read (config ``cities``: London, Berlin, Paris, Barcelona,
Madrid, Lisbon, Milan, Ibiza, New York), and in each only the music kinds this site is about
(``filters``: DJ, party, gig): a city's browse page (``/browse/<city-id>/music/<kind>``, Next.js)
embeds its ~30 top events in ``__NEXT_DATA__`` with no further page, so a run is cities x kinds
requests (~27). robots.txt allows every path to every crawler but the AI-training ones.

A row gives id (the page is ``/event/<id>``), name, sold-out status, square artwork, start and end
with the venue's time zone, the venue (name, street address, city, country) and the "from" price
in minor units. The coordinates on a row are the city's centre, not the venue's: they are left out
and the address geocodes. The kind page that listed an event is its genre.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, next_data, parse_iso, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class DiceSettings(SourceSettings):
    site_url: str = "https://dice.fm"
    # The browse ids of the cities read (dice.fm/browse/<city> redirects to them).
    cities: list[str] = Field(
        default_factory=lambda: [
            "london-54d8a23438fe5d27d500001c",
            "berlin-5e426dbb749e68e3e923d1e4",
            "paris-5b23e8a0e63cc224a4c36a2d",
            "barcelona-5d8cefe1e3e6e374e99e8cbe",
            "madrid-5d8cef380de1e404dc962211",
            "lisbon-5db997662b6fd8302bc10d91",
            "milano-5d68e89f7f285e094f60b00e",
            "ibiza-5e68e75a86c4a10280c01a29",
            "new_york-5bbf4db0f06331478e9b2c59",
        ]
    )
    # Browse filter path -> the genre it gives.
    filters: dict[str, str] = Field(
        default_factory=lambda: {"music/dj": "dj set", "music/party": "party", "music/gig": "concert"}
    )


class DiceScraper(BaseScraper[DiceSettings]):
    name = "dice"
    settings_model = DiceSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        rows: dict[str, tuple[dict[str, Any], list[str]]] = {}
        for city in s.cities:
            for path, genre in s.filters.items():
                url = f"{s.site_url}/browse/{city}/{path}?lng=en"
                page = await self.optional(url, self.get_text(url))
                try:
                    found = browse_rows(page) if page else []
                except ParseError as exc:  # a changed page: the other cities still count
                    self.record_failure(url, exc)
                    continue
                for row in found:
                    rows.setdefault(row["id"], (row, []))[1].append(genre)
        if not rows:
            raise ParseError("no browse page lists an event")
        self.log.debug("listed %d events", len(rows))
        retention = RetentionSettings()
        for row, genres in rows.values():
            try:
                event = parse_row(row, genres, s.site_url, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event


def browse_rows(page_html: str) -> list[dict[str, Any]]:
    """The events a browse page embeds (ParseError without its ``__NEXT_DATA__``)."""
    props = (next_data(page_html).get("props") or {}).get("pageProps") or {}
    events = props.get("events")
    return [e for e in events if isinstance(e, dict) and clean_str(e.get("id"))] if isinstance(events, list) else []


def parse_row(row: dict[str, Any], genres: list[str], site_url: str, timezone: str) -> ScrapedEvent | None:
    """One browse row; None without a name or a start."""
    dates = row.get("dates") if isinstance(row.get("dates"), dict) else {}
    venue = next((v for v in row.get("venues") or [] if isinstance(v, dict)), {})
    city = venue.get("city") if isinstance(venue.get("city"), dict) else {}
    tz = clean_str(dates.get("timezone")) or clean_str((city.get("location") or {}).get("place")) or timezone
    start, end = parse_iso(dates.get("event_start_date"), tz), parse_iso(dates.get("event_end_date"), tz)
    title = clean_str(row.get("name"))
    if not title or not start:
        return None
    start, end = start.astimezone(zone(tz)), end.astimezone(zone(tz)) if end else None
    price = row.get("price") if isinstance(row.get("price"), dict) else {}
    cents = price.get("amount_from") if price.get("amount_from") is not None else price.get("amount")
    low = Decimal(int(cents)) / 100 if isinstance(cents, int | float) and cents > 0 else None
    sold_out = row.get("status") == "sold-out"
    images = row.get("images") if isinstance(row.get("images"), dict) else {}
    image = clean_str(images.get("square") or images.get("landscape") or images.get("portrait"))
    country = (clean_str(city.get("country_code")) or "ZZ").upper()
    return ScrapedEvent(
        source=DiceScraper.name,
        source_event_id=row["id"],
        url=f"{site_url}/event/{row['id']}",
        title=title,
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name")),
        address=clean_str(venue.get("address")),
        city=clean_str(city.get("name")),
        country=None if country == "IL" else country,
        time_zone=None if country == "IL" else tz,
        source_genres=list(dict.fromkeys(genres)),
        images=[image] if image else [],
        currency=clean_str(price.get("currency")),
        min_price=low,
        max_price=low,
        is_sold_out=sold_out,
        tickets=[TicketTier(name="Ticket", price=low, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
        if low is not None
        else [],
    )
