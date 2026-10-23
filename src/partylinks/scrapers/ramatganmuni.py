"""Ramat Gan municipality events (ramat-gan.muni.il/event-lobby/) - culture, community, seniors.

The site is an Angular app over a public JSON API (``api-m.ramat-gan.muni.il/api``; robots.txt
allows all). ``/EventLobby/he/event-lobby`` holds every current event as cards (``closeEvents``,
``sliderEvents`` and per-category rails, ~60 unique; the theme sub-lobbies are subsets) with
title, date, venue, category and entry type. ``/Event<path>`` gives the detail: ``hour`` ("19:30"
or null), description, prices by type (resident / non-resident...), image and ``registerLink``
(Smarticket, Tickchak, Paykal, a form, Zoom...), kept as ``extra["purchase_url"]`` so dedup ties
it to the seller's own listing.

Cards under ``/events/rg-events/web-site-event-<id>/`` are the municipal Smarticket box office
mirrored (``ramat-gan`` / ``mbe-rg`` hosts, read by the smarticket source), so they are skipped
without a detail request. The rest (free municipal events, sales elsewhere) are what this adds.
A series is listed once, on its first day.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MIRRORED = "/web-site-event-"  # Smarticket box-office mirror
_FREE_ENTRY = 3  # card priceType: 0 resident benefit, 1 pre-registration, 2 pay at door, 3 free
_TIER_NAMES = {1: "לתושב רמת גן", 2: "תושב חוץ", 3: "לסטודנטים", 4: "ילד"}


class RamatGanMuniSettings(SourceSettings):
    site_url: str  # https://www.ramat-gan.muni.il
    api_url: str  # https://api-m.ramat-gan.muni.il/api
    media_url: str  # https://cms-media.ramat-gan.muni.il
    listing_path: str  # /EventLobby/he/event-lobby
    city: str


class RamatGanMuniScraper(BaseScraper[RamatGanMuniSettings]):
    name = "ramatganmuni"
    settings_model = RamatGanMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_json(s.api_url + s.listing_path, expect=dict))
        self.log.info("listed %d events (box-office mirrors skipped)", len(cards))

        async def fetch(card: dict[str, Any]) -> ScrapedEvent:
            detail = await self.get_json(s.api_url + "/Event" + card["detailsLink"]["url"], expect=dict)
            return parse_event(detail, card, s.site_url, s.media_url, s.city, self.timezone)

        async for event in self.stream_isolated(cards, fetch, label=lambda c: c["detailsLink"]["url"]):
            yield event


def parse_listing(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every card of the lobby once, box-office mirrors left out."""
    content = payload.get("content") or {}
    rails = [content.get("closeEvents"), content.get("sliderEvents")]
    rails += [cat.get("events") for cat in content.get("eventLobbyCategories") or []]
    cards: dict[str, dict[str, Any]] = {}
    for card in (c for rail in rails for c in rail or []):
        path = clean_str((card.get("detailsLink") or {}).get("url"))
        if path and _MIRRORED not in path:
            cards.setdefault(path, card)
    return list(cards.values())


def parse_event(
    payload: dict[str, Any], card: dict[str, Any], site_url: str, media_url: str, city: str, timezone: str
) -> ScrapedEvent:
    path = card["detailsLink"]["url"]
    d = payload.get("content") or {}
    title = clean_str(d.get("title")) or clean_str(card.get("title"))
    day = str(d.get("date") or card.get("date") or "")[:10]
    if not title or not day:
        raise ParseError(f"{path}: no title or date")
    hh, _, mm = str(d.get("hour") or "").partition(":")
    clock = time(int(hh) % 24, int(mm)) if hh.isdigit() and mm.isdigit() else time()
    start = datetime.combine(date.fromisoformat(day), clock, zone(timezone))

    tiers = [
        TicketTier(name=_TIER_NAMES.get(p.get("type"), "כרטיס"), price=price, is_free=price == 0)
        for p in d.get("prices") or []
        if (price := to_decimal(p.get("price"))) is not None
    ]
    if not tiers and card.get("priceType") == _FREE_ENTRY:
        tiers = [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)]
    prices = sorted(t.price for t in tiers if t.price is not None)
    place = d.get("location") or card.get("eventLocation") or {}
    media = (d.get("media") or {}).get("link")
    purchase = clean_str(d.get("registerLink"))
    category = clean_str((card.get("category") or {}).get("name"))
    return ScrapedEvent(
        source=RamatGanMuniScraper.name,
        source_event_id=path.strip("/").removeprefix("events/"),
        url=site_url + path,
        title=title,
        description=html_to_text(d.get("description")),
        start_at=start,
        venue_name=clean_str(place.get("name")) or clean_str(card.get("location")),
        address=clean_str(place.get("address")),
        city=city,
        source_genres=[category] if category else [],
        organizer=clean_str((d.get("eventSource") or card.get("eventSource") or {}).get("value")) or f"עיריית {city}",
        image_url=media_url + quote(media) if media else None,
        min_price=prices[0] if prices else None,
        max_price=prices[-1] if prices else None,
        tickets=tiers,
        extra={"purchase_url": purchase} if purchase and purchase.startswith("http") else {},
    )
