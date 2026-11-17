"""GETIN (get-in.com, and its new platform getin.xyz) - an Israeli-founded ticketing platform for club
nights and festivals, now mostly abroad (Miami, New York, Toronto, Dubai, Mexico, Uruguay) plus a few
Israeli nights. Producers sell on it directly, so its pages are the seller.

Listing: ``POST api.get-in.com/api/filter-events`` (the site's own call, no key) answers at most ~32
upcoming events per filter, so it is asked once without a filter, once for Israel, and once per
category from ``/api/categories`` (~11); the rows are merged by id (~35 events, ~13 requests). A row
gives title, local start/end with the venue's time zone, address, city, country code, coordinates,
price range, category ids, artists and the cover file. Events moved to the new platform carry
``new_platform_url`` (getin.xyz/event/<id>), their public page; the others live at get-in.com/en/<id>.

Details: each event's page (one request each) gives the description and the currency (its JSON-LD
``priceCurrency`` on getin.xyz, the page state's ``currency`` on get-in.com). The old pages' JSON-LD
stamps the local time with "Z", so times always come from the row. robots.txt: get-in.com has none;
getin.xyz disallows /api/ (its own host, never called) and the account pages.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    schema_types,
    to_decimal,
    to_float,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CURRENCY = re.compile(r'"(?:priceCurrency|currency)"\s*:\s*"([A-Z]{3})"')
_EVENT_TYPES = {"event", "musicevent", "festival", "danceevent"}


class GetinSettings(SourceSettings):
    api_url: str = "https://api.get-in.com/api"
    site_url: str = "https://get-in.com"
    # The filters asked besides "no filter": the listing caps each answer at ~32 events.
    extra_filters: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"location": {"country": "IL"}}, {"type": "week"}, {"type": "trending"}]
    )
    image_base: str = "https://static.get-in.com/gallery/tablet"  # + the row's "file" (_cover_..._<id>.png)
    # A row with no category and no artist genre (a private birthday night): it is a club night.
    default_genre: str = "nightlife"


class GetinScraper(BaseScraper[GetinSettings]):
    name = "getin"
    settings_model = GetinSettings

    def default_headers(self) -> dict[str, str]:
        return {"X-Requested-With": "XMLHttpRequest", "Origin": self.settings.site_url}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        url = f"{s.api_url}/filter-events"
        first = await self.post_json(url, json={"tab": "events"})
        rows = merge_rows([], first)
        if not rows:
            raise ParseError("filter-events lists no event")
        categories = await self.optional("categories", self.get_json(f"{s.api_url}/categories"))
        filters = [*s.extra_filters, *({"categories": [c["id"]]} for c in category_list(categories))]
        for body in filters:
            answer = await self.optional(f"filter {body}", self.post_json(url, json={"tab": "events", **body}))
            rows = merge_rows(rows, answer)
        self.log.debug("listed %d events", len(rows))
        names = {c["id"]: c["name"] for c in category_list(categories)}
        retention = RetentionSettings()

        async def fetch(row: dict[str, Any]) -> ScrapedEvent | None:
            event = parse_row(row, names, s, self.timezone)
            if event is None or not is_current(event.start_at, event.end_at, retention, self.timezone):
                return None
            page = await self.optional(str(event.url), self.get_text(str(event.url)))
            return with_page(event, page) if page else event

        async for event in self.stream_isolated(rows, fetch, label=lambda r: str(r.get("event_id"))):
            if event:
                yield event


def category_list(answer: Any) -> list[dict[str, Any]]:
    """``/api/categories``: id and English name of each category with active events."""
    rows = answer.get("data") if isinstance(answer, dict) else answer
    out = []
    for c in rows if isinstance(rows, list) else []:
        if isinstance(c, dict) and isinstance(c.get("id"), int):
            names = c.get("name") if isinstance(c.get("name"), dict) else {}
            out.append({"id": c["id"], "name": clean_str(names.get("en") or names.get("he")) or ""})
    return out


def merge_rows(rows: list[dict[str, Any]], answer: Any) -> list[dict[str, Any]]:
    """The answer's event rows added to ``rows``, once per id, in order."""
    seen = {r.get("event_id") for r in rows}
    events = answer.get("events") if isinstance(answer, dict) else None
    for row in events if isinstance(events, list) else []:
        if isinstance(row, dict) and row.get("event_id") is not None and row["event_id"] not in seen:
            seen.add(row["event_id"])
            rows.append(row)
    return rows


def _venue(row: dict[str, Any]) -> dict[str, Any]:
    return row["venue"] if isinstance(row.get("venue"), dict) else {}


def parse_row(row: dict[str, Any], categories: dict[int, str], s: GetinSettings, timezone: str) -> ScrapedEvent | None:
    """One listing row; None without a title or a start."""
    venue = _venue(row)
    country = (clean_str(row.get("country_short_name")) or clean_str(venue.get("country_code")) or "").upper() or None
    abroad = country not in (None, "IL")
    city = clean_str(row.get("city")) or clean_str(venue.get("city"))
    tz = clean_str(row.get("time_zone")) or (local_zone(country, timezone, city) if abroad else timezone)
    title, start, end = clean_str(row.get("title")), _local(row.get("start_date"), tz), _local(row.get("end_date"), tz)
    if not title or not start:
        return None
    new = row.get("is_new_platform") and clean_str(row.get("new_platform_url"))
    page = _plain(new) if new else f"{s.site_url}/en/{row['event_id']}"
    lat = to_float(venue.get("lat") or row.get("lat"))
    lon = to_float(venue.get("lon") or row.get("lon"))
    images = row.get("new_platform_images") if isinstance(row.get("new_platform_images"), dict) else {}
    image = clean_str(images.get("desktop")) or (f"{s.image_base}{row['file']}" if clean_str(row.get("file")) else None)
    artists = [a for a in row.get("artists") or [] if isinstance(a, dict) and clean_str(a.get("name"))]
    genres = [categories[c] for c in row.get("categories") or [] if categories.get(c)]
    genres += [
        g for a in row.get("artists") or [] if isinstance(a, dict) for g in a.get("genres") or [] if isinstance(g, str)
    ]
    low, high = to_decimal(row.get("min_price")), to_decimal(row.get("max_price"))
    return ScrapedEvent(
        source=GetinScraper.name,
        source_event_id=str(row["event_id"]),
        url=page,
        title=title,
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name"))
        or clean_str(row.get("venue") if isinstance(row.get("venue"), str) else None),
        address=clean_str(venue.get("address")) or clean_str(row.get("address")),
        city=city,
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=list(dict.fromkeys(g.strip().lower() for g in genres if g.strip())) or [s.default_genre],
        artists=[a["name"].strip() for a in artists],
        artist_profiles=[
            ArtistProfile(name=a["name"].strip(), source=GetinScraper.name, image_url=clean_str(a.get("image")))
            for a in artists
        ],
        images=[image] if image else [],
        organizer=clean_str(row.get("producer_name")),
        # Prices only once the page names the currency (abroad it is the venue's, not ILS).
        extra={"price_range": [str(low) if low else None, str(high) if high else None]} if low or high else {},
    )


def with_page(event: ScrapedEvent, page_html: str) -> ScrapedEvent:
    """The event page's description and currency (then the listing's prices)."""
    node = next((n for n in jsonld_nodes(page_html) if schema_types(n) & _EVENT_TYPES), {})
    description = html_to_text(str(node.get("description") or "")) or event.description
    currency = (m := _CURRENCY.search(page_html)) and m.group(1)
    update: dict[str, Any] = {"description": description}
    low, high = (event.extra.get("price_range") or [None, None]) if event.extra else (None, None)
    if currency and (low or high):
        lo, hi = Decimal(low) if low else None, Decimal(high) if high else None
        update |= {
            "currency": currency,
            "min_price": lo or hi,
            "max_price": hi or lo,
            "tickets": [TicketTier(name="כרטיס", price=lo or hi, status=TierStatus.ON_SALE)],
        }
    update["extra"] = {k: v for k, v in (event.extra or {}).items() if k != "price_range"}
    return event.model_copy(update=update)


def _local(value: Any, tz: str) -> datetime | None:
    """ "2026-12-02 20:00:00" in the venue's zone."""
    text = clean_str(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone(tz)) if text else None
    except ValueError:
        return None


def _plain(url: str) -> str:
    """The page without the platform's seller code (?seller_code=getin)."""
    return urlunsplit(urlsplit(url)._replace(query="", fragment=""))
