"""iTravelJerusalem (itraveljerusalem.com): the Jerusalem tourism board's events calendar ("לוח אירועים
בירושלים"): Christmas in the Old City (the YMCA tree lighting, Santa's house), Hanukkah festivals,
guided tours, concerts, exhibitions, markets at the First Station.

A Next.js site over a Strapi CMS; every page carries its data in ``__NEXT_DATA__``. The events list
(``/he/list/events``, 20 cards a page, soonest first, ``?pageNumber=N`` to the last page) holds only
the coming events. Its filters (``filters.event_types``, ``filters.seasons``: "סיורים", "הצגות",
"חנוכה", "חג המולד"...) narrow it by ``?event_types=<id>`` / ``?seasons=<id>``: each filter with
events is read to label the events with the site's categories. Each event's page (``/he/event/<slug>``)
gives every coming date (``event_dates``: the start in UTC and the local end time), the text and
its notes, the address and, for some, the pin, the price, the ticket link and the pictures.

Hebrew pages are preferred: the English list (``/list/events``) adds only the events that have no
Hebrew version (an event's Hebrew page names its English one in ``localizations``). Not read: the
other languages (de, fr, ru, es, it, ar: translations of the same events), the sitemap (~800
/event/ URLs: every past event too; the list holds every coming one), the other lists
(attractions, restaurants, hotels, tours: places, no dates).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, next_data, parse_iso, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class ITravelJerusalemSettings(SourceSettings):
    site_url: str = "https://www.itraveljerusalem.com"
    list_path: str = "/list/events"
    event_path: str = "/event/"
    locales: list[str] = Field(default_factory=lambda: ["he", "en"])  # the first one preferred
    # The list's filters read for the site's categories (event types, seasons: "חנוכה", "חג המולד").
    label_filters: list[str] = Field(default_factory=lambda: ["event_types", "seasons"])
    # Filter values that are no category: the weather seasons and the site's promotions.
    ignore_labels: list[str] = Field(default_factory=lambda: ["חורף", "קיץ", "אביב", "סתיו", "כל השנה", "זום"])
    city: str = "ירושלים"
    organizer: str = "iTravelJerusalem"
    social_hosts: list[str] = Field(default_factory=lambda: ["facebook.com", "instagram.com", "wa.me", "whatsapp.com"])


@dataclass
class Card:
    id: int
    slug: str
    locale: str
    latitude: float | None = None
    longitude: float | None = None
    labels: list[str] = field(default_factory=list)


class ITravelJerusalemScraper(BaseScraper[ITravelJerusalemSettings]):
    name = "itraveljerusalem"
    settings_model = ITravelJerusalemSettings

    def _url(self, locale: str, path: str) -> str:
        prefix = "" if locale == "en" else f"/{locale}"
        return f"{self.settings.site_url}{prefix}{path}"

    async def _props(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        props = next_data(await self.get_text(url, params=params)).get("props", {}).get("pageProps")
        if not isinstance(props, dict):
            raise TypeError(f"no pageProps in {url}")
        return props

    async def _list(
        self, locale: str, extra: dict[str, str] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Every card of the list (a filter of it), page by page to the last one, and the first page's props."""
        url = self._url(locale, self.settings.list_path)
        first = await self._props(url, extra)
        cards = list(first.get("listingCards") or [])
        pages = int((first.get("listingCardsPagination") or {}).get("pageCount") or 1)
        for number in range(2, pages + 1):
            page = await self.optional(
                f"{locale} list page {number}", self._props(url, {**(extra or {}), "pageNumber": str(number)})
            )
            cards.extend((page or {}).get("listingCards") or [])
        return cards, first

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        seen_ids: set[int] = set()  # the other locales' ids of the events read (from localizations)
        seen_docs: set[str] = set()
        for index, locale in enumerate(s.locales):
            listing = await (self._list(locale) if index == 0 else self.optional(f"{locale} list", self._list(locale)))
            if listing is None:
                continue
            raw_cards, first = listing
            cards = [c for c in (card_of(r, locale) for r in raw_cards) if c and c.id not in seen_ids]
            if index == 0:
                await self._label(cards, first)

            async def one(card: Card) -> list[ScrapedEvent]:
                props = await self._props(self._url(card.locale, s.event_path + card.slug))
                event = props.get("event")
                if not isinstance(event, dict) or str(event.get("documentId")) in seen_docs:
                    return []
                seen_docs.add(str(event.get("documentId")))
                seen_ids.update(
                    int(loc["id"])
                    for loc in event.get("localizations") or []
                    if isinstance(loc, dict) and loc.get("id")
                )
                return parse_event(event, card, s, self.timezone)

            async for events in self.stream_isolated(cards, one, lambda c: f"{c.locale} event {c.slug}"):
                for event in events:
                    if is_current(event.start_at, event.end_at, retention, self.timezone):
                        yield event

    async def _label(self, cards: list[Card], first: dict[str, Any]) -> None:
        """The site's categories of each card: the filters of the list that hold it."""
        by_id = {c.id: c for c in cards}
        filters = first.get("filters") or {}
        for name in self.settings.label_filters:
            for value, info in (filters.get(name) or {}).items():
                title = clean_str((info or {}).get("title"))
                if not title or not (info or {}).get("count") or title in self.settings.ignore_labels:
                    continue
                listing = await self.optional(
                    f"filter {name}={value}", self._list(self.settings.locales[0], {name: str(value)})
                )
                for raw in (listing or ([], {}))[0]:
                    card = by_id.get(int(raw.get("id") or 0)) if isinstance(raw, dict) else None
                    if card and title.lower() not in card.labels:
                        card.labels.append(title.lower())


def card_of(raw: dict[str, Any], locale: str) -> Card | None:
    """A list card: the event's id, its page's slug, the pin when the card has one."""
    path = str(raw.get("urlPath") or "")
    if not raw.get("id") or "/event/" not in path:
        return None
    loc = raw.get("location") or {}
    lat, lng = loc.get("Latitude"), loc.get("Longitude")
    return Card(
        id=int(raw["id"]),
        slug=path.rsplit("/event/", 1)[1].strip("/"),
        locale=locale,
        latitude=float(lat) if lat is not None and lng is not None else None,
        longitude=float(lng) if lat is not None and lng is not None else None,
    )


def _clock(value: Any) -> time | None:
    text = str(value or "")
    try:
        hour, minute = int(text[:2]), int(text[3:5])
    except ValueError:
        return None
    return time(hour, minute) if hour < 24 and minute < 60 else None


def _image(media: Any) -> str | None:
    return clean_str(media.get("url")) if isinstance(media, dict) else None


def parse_event(event: dict[str, Any], card: Card, s: ITravelJerusalemSettings, timezone: str) -> list[ScrapedEvent]:
    """One event per coming date of the event's page."""
    tz = zone(timezone)
    title = clean_str(event.get("name"))
    if not title:
        return []
    notes = [html_to_text(n.get("content")) for n in event.get("notes") or [] if isinstance(n, dict)]
    description = (
        "\n\n".join(p for p in (clean_str(event.get("subtitle")), html_to_text(event.get("body")), *notes) if p) or None
    )
    address = clean_str(event.get("address"))
    venue = clean_str(re.split(r"[,|]", address)[0]) if address else None  # "תיאטרון ירושלים | מרכוס 20, ירושלים"
    loc = event.get("location") or {}
    lat = loc.get("Latitude") if loc.get("Longitude") is not None else None
    lng = loc.get("Longitude") if lat is not None else None
    if lat is None and card.latitude is not None:
        lat, lng = card.latitude, card.longitude
    images = [i for i in [_image(event.get("preview")), _image(event.get("bigImage"))] if i]
    for item in event.get("gallery") or []:
        if (
            isinstance(item, dict)
            and not item.get("isHidden")
            and (url := _image(item.get("image")))
            and url not in images
        ):
            images.append(url)
    link = clean_str(event.get("getTickets_url")) or clean_str(event.get("website"))
    if link and (not link.startswith("http") or any(h in link for h in s.social_hosts)):
        link = None
    price = event.get("priceAmount") if event.get("priceAmount") is not None else event.get("minPrice")
    tickets = []
    if price is not None and str(event.get("priceCurrency") or "ils").lower() == "ils":
        amount = Decimal(str(price))
        tickets = [TicketTier(name="כניסה חופשית" if amount == 0 else "כרטיס", price=amount, is_free=amount == 0)]
    url = f"{s.site_url}{'' if card.locale == 'en' else '/' + card.locale}{s.event_path}{card.slug}"
    events = []
    for occurrence in event.get("event_dates") or []:
        if not isinstance(occurrence, dict):
            continue
        start = parse_iso(occurrence.get("date"))
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        start = start.astimezone(tz)
        end = None
        if end_clock := _clock(occurrence.get("endTime")):
            end = datetime.combine(start.date(), end_clock, tz)
            if end <= start:
                end += timedelta(days=1)
        events.append(
            ScrapedEvent(
                source="itraveljerusalem",
                source_event_id=f"{event.get('documentId') or event.get('id')}-{start.strftime('%Y%m%dT%H%M')}",
                url=url,
                title=title,
                description=description,
                start_at=start,
                end_at=end,
                time_known=True,
                venue_name=venue,
                address=address,
                city=s.city,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lng) if lng is not None else None,
                source_genres=list(card.labels),
                organizer=s.organizer,
                images=images,
                tickets=tickets,
                is_sold_out=bool(event.get("fullyBooked")),
                extra={"purchase_url": link} if link else {},
            )
        )
    return events
