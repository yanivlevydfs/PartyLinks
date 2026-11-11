"""Daniel Tel Aviv (gototlv.co.il) - a hand-picked two-week guide to Tel Aviv: parties, shows, culture, free stuff.

The page is an empty shell; its ``app.js`` loads ``events.json`` (one request, the whole edition,
~30 events) and ``config.json`` (the edition's window). Each row has a slug id, title, ISO ``date``
and optional ``endDate``, a free-text ``time`` ("23:00 עד 06:00 למחרת", "חמישי 19:00-23:30",
"שעות משתנות לפי מתחם"), ``location`` ("venue, street"), the guide's own ``category`` (מסיבות,
תרבות, מוזיקה, ירידים), ``price`` (number, 0 = free, null = unknown) with a ``priceText``, the
seller or official page as ``url`` (sometimes an Instagram post), an optional ``sourceUrl`` and
``expiresAt``, the moment the guide stops showing it (the event's end). There is no page per event,
no image and no coordinates: the street address is geocoded in Tel Aviv, and the artwork is the
linked page's og:image (one request per new event; a logo is skipped). Most parties link to
Go-Out / Eventer / Chillz, which dedup merges through the purchase link. No robots.txt (the SPA
answers every path with its index).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, og_image, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import says_free

_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


class GotoTlvSettings(SourceSettings):
    site_url: str = "https://gototlv.co.il"
    events_path: str = "/events.json"
    city: str = "תל אביב-יפו"
    social_hosts: list[str] = Field(default_factory=lambda: ["instagram.com", "facebook.com", "tiktok.com"])


class GotoTlvScraper(BaseScraper[GotoTlvSettings]):
    name = "gototlv"
    settings_model = GotoTlvSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        rows = await self.get_json(self.settings.site_url + self.settings.events_path, expect=list)
        self.log.debug("listed %d events", len(rows))
        events: list[ScrapedEvent] = []
        for row in rows:
            try:
                event = parse_event(row, self.settings, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        async for event in self.stream_isolated(events, self._with_artwork, lambda e: e.source_event_id):
            yield event

    async def _with_artwork(self, event: ScrapedEvent) -> ScrapedEvent:
        """The guide has no images: the linked page's og:image (media.py stores it as WebP)."""
        if known := self.known_images.get(event.source_event_id):
            event.images = known
        elif (link := event.extra.get("purchase_url")) and self.wanted(event):
            page = await self.optional(f"artwork {event.source_event_id}", self.get_text(link))
            if page and (image := artwork(page)):
                event.images = [image]
        return event


def artwork(page_html: str) -> str | None:
    """The page's og:image unless it is the site's logo."""
    image = og_image(page_html)
    return image if image and image.startswith("https://") and "logo" not in image.lower() else None


def _social(url: str | None, s: GotoTlvSettings) -> bool:
    host = (urlsplit(url or "").hostname or "").removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in s.social_hosts)


def parse_event(row: dict[str, Any], s: GotoTlvSettings, timezone: str) -> ScrapedEvent | None:
    """One guide row; None when unpublished."""
    if row.get("published") is False:
        return None
    event_id, title, day = clean_str(row.get("id")), clean_str(row.get("title")), clean_str(row.get("date"))
    if not event_id or not title or not day:
        raise ParseError(f"row without id, title or date: {row!r:.120}")
    tz = zone(timezone)
    when = clean_str(row.get("time")) or ""
    clock = _CLOCK.search(when)
    start = datetime.combine(
        datetime.fromisoformat(day).date(), time(int(clock[1]), int(clock[2])) if clock else time(0), tz
    )
    end = datetime.fromisoformat(row["expiresAt"]).astimezone(tz) if row.get("expiresAt") else None
    if end is not None and end <= start:
        end = None

    # "venue, street"; "האוזן; פרטי הכניסה בכרטיס" is a venue with a note.
    venue, _, street = (clean_str(row.get("location")) or "").split(";")[0].partition(",")
    venue, street = clean_str(venue), clean_str(street)
    links = [u for u in (clean_str(row.get("url")), clean_str(row.get("sourceUrl"))) if u and u.startswith("https://")]
    purchase = next((u for u in links if not _social(u, s)), None)

    price, label = to_decimal(row.get("price")), clean_str(row.get("priceText"))
    free = price == 0 or (price is None and says_free(label))
    tickets = (
        [TicketTier(name=label or "כניסה", price=Decimal(0) if free else price, is_free=free)] if free or price else []
    )
    category = clean_str(row.get("category"))
    return ScrapedEvent(
        source=GotoTlvScraper.name,
        source_event_id=event_id,
        url=purchase or f"{s.site_url}/#{event_id}",
        title=title,
        description="\n\n".join(x for x in (clean_str(row.get("description")), when) if x) or None,
        start_at=start,
        end_at=end,
        time_known=clock is not None,
        venue_name=venue,
        address=f"{street or venue}, {s.city}" if venue else None,
        city=s.city,
        source_genres=[category.lower()] if category else [],
        tickets=tickets,
        min_price=tickets[0].price if tickets else None,
        max_price=tickets[0].price if tickets else None,
        extra={"purchase_url": purchase},
    )
