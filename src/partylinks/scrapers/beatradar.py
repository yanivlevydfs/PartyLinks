"""Beat Radar (beatradar.co.il) - a party guide that gathers Go-Out and Selector parties in one list.

A React app on a public JSON API; robots.txt is absent. Listing: ``/api/events`` returns every
upcoming party in one request (~120): Hebrew and English title and summary, start/end, venue and
city, ticket tiers with prices, artists, minimum age and the site's genre tags. Each party links to
its ticket page on Go-Out or Selector (``metadata.url``), which is the link kept here; dedup merges
it with the same party scraped from that site. Genre tags and event types (``danceParty``,
``djSet``...) become source genres in plain words.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
# Beat Radar's words our genre keywords do not know, in words they do.
_GENRE_WORDS = {"live": "live show", "festival": "music festival", "groove": "funk", "black music": "hip hop r&b"}
# A city or venue named only for the country or a region is no place.
_NO_PLACE = {"israel", "ישראל", "north district", "northern district", "center district", "central district",
             "central israel", "south district", "southern district", "מרכז הארץ", "צפון הארץ", "דרום הארץ"}
_CURRENCIES = {"NIS": "ILS", "₪": "ILS"}


class BeatRadarSettings(SourceSettings):
    site_url: str
    events_path: str


class BeatRadarScraper(BaseScraper[BeatRadarSettings]):
    name = "beatradar"
    settings_model = BeatRadarSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Cloudflare's block page (403): the API answers Israeli home connections only (Oct 2026), so a
        foreign, proxy or datacenter IP is refused whatever the client looks like. A blocked run, not a crash."""
        return response.status_code == 403 and response.headers.get("server", "").lower() == "cloudflare"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items = await self.get_json(s.site_url + s.events_path, expect=list)
        self.log.info("listed %d events", len(items))
        for item in items:
            try:
                event = parse_event(item, self.timezone)
            except ParseError as exc:
                self.record_failure(str(item.get("_id")), exc)
                continue
            if event is not None:
                yield event


def parse_event(item: dict[str, Any], timezone: str) -> ScrapedEvent | None:
    """One party; None when it is hidden on the site."""
    meta = item.get("metadata") or {}
    if meta.get("visibility") not in (None, "all"):
        return None
    event_id, url = clean_str(item.get("_id")), _without_ref(clean_str(meta.get("url")))
    title = _text(item.get("title"), "original", "he", "en")  # "he" is often a machine translation
    start = parse_iso(item.get("startTimestamp"), timezone)
    if not event_id or not url or not title or not start:
        raise ParseError(f"{event_id}: party without id, link, title or start")
    location = item.get("location") or {}
    venue = _place(location.get("venueName_original") or location.get("venueName"))
    address = clean_str(location.get("fullAddress"))
    tickets = [_tier(t) for t in item.get("tickets") or [] if isinstance(t, dict)]
    prices = [t.price for t in tickets if t.price is not None]
    genres = item.get("genres") or {}
    types = [_CAMEL.sub(" ", t).lower() for t in item.get("eventType") or [] if isinstance(t, str)]
    image = ((item.get("images") or {}).get("main") or {}).get("original")
    return ScrapedEvent(
        source=BeatRadarScraper.name,
        source_event_id=event_id,
        url=url,
        title=title,
        description=_text(item.get("summary"), "he", "en"),
        start_at=start,
        end_at=parse_iso(item.get("endTimestamp"), timezone),
        venue_name=venue,
        address=address if address != venue else None,
        city=_place(location.get("city_original") or location.get("city")),
        latitude=location.get("lat"),
        longitude=location.get("lng"),
        source_genres=list(
            dict.fromkeys(
                _GENRE_WORDS.get(g.lower(), g)
                for g in [*genres.get("en", []), *genres.get("he", [])]
                if isinstance(g, str)
            )
        ),
        event_types=[_GENRE_WORDS.get(t, t) for t in types if t != "other"],
        artists=[a for a in (clean_str(a) for a in item.get("artists") or []) if a],
        min_age=item.get("minAgeLimit") if isinstance(item.get("minAgeLimit"), int) else None,
        organizer=", ".join(
            n for n in (clean_str(p.get("name")) for p in item.get("producers") or [] if isinstance(p, dict)) if n
        )
        or None,
        image_url=image if isinstance(image, str) else None,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        tickets=tickets,
        is_sold_out=bool(tickets) and all(t.status is TierStatus.SOLD_OUT for t in tickets),
    )


def _tier(ticket: dict[str, Any]) -> TicketTier:
    """A tier; a price of 0 with no base price is unknown, not free (some tiers read 0 throughout)."""
    final, base = to_decimal(ticket.get("priceFinal")), to_decimal(ticket.get("priceBase"))
    price = final or base
    # The final price carries the fee as a float (108.0009).
    price = price.quantize(Decimal("0.01")) if price else None
    return TicketTier(
        name=_text(ticket.get("type"), "he", "original", "en") or "כרטיס",
        price=price,
        currency=_CURRENCIES.get(str(ticket.get("priceCurrency") or "NIS"), str(ticket.get("priceCurrency"))),
        status=TierStatus.ON_SALE if ticket.get("inStock", True) else TierStatus.SOLD_OUT,
        sale_start=parse_iso(ticket.get("validFrom")),
        sale_end=parse_iso(ticket.get("validThrough")),
    )


def _text(value: Any, *langs: str) -> str | None:
    """The first language present of a {"he": .., "en": ..} field."""
    if not isinstance(value, dict):
        return clean_str(value)
    return next((t for t in (clean_str(value.get(lang)) for lang in langs) if t), None)


def _place(value: Any) -> str | None:
    place = clean_str(value)
    return None if place is None or place.casefold() in _NO_PLACE else place


def _without_ref(url: str | None) -> str | None:
    """The ticket link without Beat Radar's own ``ref`` tag (ours is added on the way out)."""
    if not url:
        return None
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "ref"])
    return urlunsplit(parts._replace(query=query))
