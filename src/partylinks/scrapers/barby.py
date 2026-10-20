"""Barby (barby.co.il) - Tel Aviv live-music club.

Listing: ``GET /api/shows/find`` returns every upcoming show in one payload.
Details per show, fetched in parallel: ``/api/shows/show/<id>`` (description,
sold-out flag) and ``/api/shows/tier-price/<id>`` (ticket tiers with quantity
and sold count). ``tierPriceType`` is 1 = single price, 2 = sequential rounds
(early bird, regular...), 3 = parallel tiers (adult / child).
Artists: the site has no artist data; the headliner leads ``showName`` ("איתי לוי - בבארבי",
"ויתרתי VS כוח וידאו", "... אורחת אפרת גוש") and ``showGuestsNames`` is rarely filled.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

SINGLE_PRICE = "1"

# Themed nights / tributes / non-shows: the name is not an artist.
_NOT_ARTIST = re.compile(r"מסיבת|חוזרת ל|מחווה|tribute|שירות הלקוחות|הלילה", re.IGNORECASE)
_GUESTS = re.compile(r"אורח(?:ת|ים)?(?:\s+מיוחד(?:ת|ים)?)?(?![֐-׿])\s*[:\-]?\s*(.+)$")
_HEAD_END = re.compile(r"\s+-\s*|-\s+|\s*:\s*|,|(?<=\S)-(?=חוגג|מופע|לאור)")
_TAIL = re.compile(r"\s*(?:בבארבי|מגיע(?:ה|ים)? לבארבי.*|חוזר(?:ת)? לבארבי.*|לראשונה.*|לייב|והלהקה|במופע.*|חוגג(?:ת)?.*)\s*$")
_ACTS = re.compile(r"\s+(?:VS|x|&|\+)\s+", re.IGNORECASE)
_NAMES = re.compile(r"\s*,\s*|\s+ו(?=\S)")


class BarbySettings(SourceSettings):
    site_url: str
    list_path: str
    show_path: str
    tiers_path: str
    event_page_path: str
    image_url_template: str
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class BarbyScraper(BaseScraper[BarbySettings]):
    name = "barby"
    settings_model = BarbySettings

    def default_headers(self) -> dict[str, str]:
        return {"Referer": self.settings.site_url + "/"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        payload = await self.get_json(s.site_url + s.list_path, expect=dict)
        listing = payload.get("returnShow")
        shows = [show for show in (listing.get("show") if isinstance(listing, dict) else None) or [] if isinstance(show, dict)]
        self.log.info("listed %d events", len(shows))
        async for event in self.stream_isolated(shows, self._fetch_event, label=lambda show: str(show.get("showId"))):
            yield event

    async def _fetch_event(self, listed: dict[str, Any]) -> ScrapedEvent:
        s = self.settings
        show_id = listed.get("showId")
        if not show_id:
            raise ParseError("showId missing")
        # Detail/tier endpoints down -> save the show from its listing data.
        details, tiers = await asyncio.gather(
            self.optional(f"{show_id} details", self.get_json(s.site_url + s.show_path.format(show_id=show_id))),
            self.optional(f"{show_id} tiers", self.get_json(s.site_url + s.tiers_path.format(show_id=show_id))),
        )
        details = details if isinstance(details, dict) else {}
        return parse_event(listed, details, tiers, s, self.timezone)


def parse_event(
    listed: dict[str, Any], details: dict[str, Any], tiers: Any, s: BarbySettings, timezone: str
) -> ScrapedEvent:
    show_id = clean_str(listed.get("showId"))
    if not show_id:
        raise ParseError("showId missing")
    show = {**listed, **(details or {})}
    tickets = _parse_tiers(listed, tiers)
    priced = [t.price for t in tickets if t.price is not None and t.is_active]
    image = clean_str(show.get("showImage"))
    return ScrapedEvent(
        source=BarbyScraper.name,
        source_event_id=show_id,
        url=s.site_url + s.event_page_path.format(show_id=show_id),
        title=clean_str(show.get("showName")) or "",
        description=html_to_text(show.get("description")) or html_to_text(show.get("showTitle")),
        start_at=_parse_start(show.get("showDate"), show.get("showTime"), timezone),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        event_types=[t for t in (clean_str(show.get("showType")), clean_str(show.get("showSeatType"))) if t],
        artists=show_artists(show.get("showName"), show.get("showGuestsNames")),
        images=[s.image_url_template.format(image=image)] if image else [],
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=bool(show.get("isSoldOut")) or (bool(tickets) and all(t.is_sold_out for t in tickets)),
        tickets=tickets,
        extra={
            "seat_type": clean_str(show.get("showSeatType")),
            "guests": clean_str(show.get("showGuestsNames")),
            "youtube": clean_str(show.get("ytube")),
            "tier_price_type": show.get("showTierPriceType"),
        },
    )


def show_artists(show_name: Any, guests: Any) -> list[str]:
    """Headliner(s) from the show name, then guests ("אורחת: X, Y" in the name, or the guests field).

    ponytail: pattern rules over free text; a themed night that slips through is fixed with
    ``partylinks artists block``. Upgrade path: match against the artist book only.
    """
    name = " ".join(str(show_name or "").split())
    if not name or _NOT_ARTIST.search(name):
        return []
    names: list[str] = []
    if match := _GUESTS.search(name):
        names += _NAMES.split(match.group(1).strip(" ."))
        name = name[: match.start()]
    head = _TAIL.sub("", _HEAD_END.split(name)[0]).strip(" .-")
    names = [*_ACTS.split(head), *names, *_NAMES.split(" ".join(str(guests or "").split()))]
    out = [n for raw in names if (n := raw.strip(" .-")) and len(n.split()) <= 4 and not n.startswith("מיוחד")]
    return list(dict.fromkeys(out))


def _parse_tiers(listed: dict[str, Any], tiers: Any) -> list[TicketTier]:
    if isinstance(tiers, list) and tiers:
        rounds = [float(p) for t in tiers if (p := to_decimal(t.get("TierPrice"))) is not None]
        sequential = str(listed.get("showTierPriceType")) == "2"
        return [_tier(t, rounds if sequential else []) for t in tiers]
    # Single-price show: price and capacity come with the show itself.
    data = tiers if isinstance(tiers, dict) else {}
    price = to_decimal(data.get("nochair_price") or listed.get("showPrice"))
    if price is None:
        return []
    capacity = to_int(data.get("nochair_max_buy") or listed.get("showSoldMaxBuy"))
    sold = to_int(data.get("sold") if data else listed.get("showSold")) or 0
    remaining = max(capacity - sold, 0) if capacity else None
    return [
        TicketTier(
            name="כרטיס",
            price=price,
            is_free=price == 0,
            remaining=remaining,
            status=TierStatus.SOLD_OUT if remaining == 0 else TierStatus.ON_SALE,
        )
    ]


def _tier(tier: dict[str, Any], rounds: list[float]) -> TicketTier:
    price = to_decimal(tier.get("TierPrice"))
    quantity, sold = to_int(tier.get("TierQuentity")), to_int(tier.get("sold")) or 0
    remaining = max(quantity - sold, 0) if quantity else None
    return TicketTier(
        source_ticket_id=clean_str(tier.get("fee_id") or tier.get("_id")),
        name=clean_str(tier.get("TierName")) or "כרטיס",
        price=price,
        price_rounds=rounds,
        is_free=price == 0,
        remaining=remaining,
        status=TierStatus.SOLD_OUT if remaining == 0 else TierStatus.ON_SALE,
    )


def _parse_start(day: Any, time: Any, timezone: str) -> datetime | None:
    """Barby dates are local ``dd/mm/yyyy`` + ``HH:MM``."""
    day_s, time_s = clean_str(day), clean_str(time) or "00:00"
    if not day_s:
        return None
    return datetime.strptime(f"{day_s} {time_s}", "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo(timezone))
