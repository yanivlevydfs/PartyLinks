"""Petah Tikva municipality events (petah-tikva.muni.il/events/all-events) - culture, community, lectures.

The site is a Next.js front over a headless Umbraco CMS. Its event pages load from the Umbraco
Delivery API, proxied at ``api.petah-tikva.muni.il/content?content=<delivery path>`` (robots.txt
of www disallows only www's own ``/api/``; the api host has none). One request,
``filter=contentType:eventPage`` + ``filter=startDate>=<today>`` with a large ``take``, returns
every coming event (~580) with title, start / end, venue, address, price, register link, image
and the CMS's category tags. The host refuses plain clients (F5 "Request Rejected"): Chrome TLS.

Dates carry a ``Z`` but are the wall clock in Israel ("2026-11-07T21:00:00Z" is a 21:00 show).
Placeholder ends (equal to the start, or on a later day: a course's last meeting) are dropped.

Most rows (``sourceSystem`` makefet / smartticket, ~555) mirror the municipal Smarticket box
offices (``makefet`` and ``petah-tikva`` hosts, read by the smarticket source), so they are
skipped. The rest are what this adds: the municipal market (WooCommerce), Tickchak, Na'amat,
registration forms and free events. Zoom lectures are skipped (online).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class PetahTikvaMuniSettings(SourceSettings):
    site_url: str  # https://www.petah-tikva.muni.il
    api_url: str  # https://api.petah-tikva.muni.il  (the proxy, media under /media/)
    city: str
    page_size: int = 5000
    # Rows copied from Smarticket box offices the smarticket source reads.
    mirror_systems: list[str] = Field(default_factory=lambda: ["makefet", "smartticket"])
    # Halls the market rows name without an address (substring of the venue -> street, from the
    # site's own event texts), so geocoding can place them.
    venue_addresses: dict[str, str] = Field(default_factory=dict)
    online_places: list[str] = Field(default_factory=lambda: ["zoom", "זום", "אונליין", "online"])
    # Tag groups that name the kind of event; the others are audiences, halls, neighbourhoods.
    genre_groups: list[str] = Field(
        default_factory=lambda: [
            "אירועים ומפגשים",
            "בידור ואמנות",
            "ספורט ובריאות",
            "העשרה",
            "חגים ומסורת",
            "משפחתיות",
        ]
    )


class PetahTikvaMuniScraper(BaseScraper[PetahTikvaMuniSettings]):
    name = "petahtikvamuni"
    settings_model = PetahTikvaMuniSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return "Request Rejected" in response.text[:300]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).strftime("%d/%m/%Y")
        # The proxy takes the delivery path with its own "?" as is, like the site's front sends it.
        url = (
            f"{s.api_url}/content?content=/umbraco/delivery/api/v2/content?filter=contentType%3AeventPage"
            f"&filter=startDate%3E={today}&sort=startDate:asc&take={s.page_size}"
        )
        payload = await self.get_json(url, expect=dict)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ParseError("delivery API answered without items")
        for item in items:
            try:
                event = parse_event(item, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {item.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def _wall_clock(value: Any, timezone: str) -> datetime | None:
    text = clean_str(value)
    if not text:
        return None
    return datetime.fromisoformat(text.removesuffix("Z")[:19]).replace(tzinfo=zone(timezone))


def _genres(categories: Any, groups: list[str]) -> list[str]:
    out: list[str] = []
    for block in (categories or {}).get("items") or []:
        raw = ((block.get("content") or {}).get("properties") or {}).get("category")
        tag = json.loads(raw) if isinstance(raw, str) else raw or {}
        if tag.get("firstText") in groups:
            out += [g.strip().lower() for g in tag.get("secondValueText") or [] if g and g.strip()]
    return list(dict.fromkeys(out))


def parse_event(item: dict[str, Any], s: PetahTikvaMuniSettings, timezone: str) -> ScrapedEvent | None:
    """One delivery-API row; None for a Smarticket mirror or an online event."""
    p = item.get("properties") or {}
    if p.get("sourceSystem") in s.mirror_systems:
        return None
    place = clean_str(p.get("location")) or ""
    if p.get("onlineEvent") or place.casefold() in s.online_places:
        return None
    title = clean_str(p.get("title")) or clean_str(item.get("name"))
    start = _wall_clock(p.get("startDate"), timezone)
    path = (item.get("route") or {}).get("path")
    if not title or not start or not path:
        raise ParseError(f"{item.get('id')}: no title, start or page")
    end = _wall_clock(p.get("endDate"), timezone)
    if end and (end <= start or end.date() != start.date()):
        end = None

    # The place is a hall with its address under it, or an address with the hall's name under it.
    note = html_to_text((p.get("locationDescription") or {}).get("markup")) or None
    if note and any(c.isdigit() for c in note):
        venue, address = place or None, note
    elif note:
        venue, address = note, place or None
    else:
        venue, address = place or None, None
    if venue:
        address = next((a for k, a in s.venue_addresses.items() if k in venue), address)

    price = to_decimal(p.get("price"))
    tiers = (
        [TicketTier(name="כרטיס" if price else "כניסה חופשית", price=price, is_free=price == 0)]
        if price is not None
        else []
    )
    link = next((clean_str(r.get("url")) for r in p.get("registerLink") or [] if clean_str(r.get("url"))), None)
    media = next((m.get("url") for m in p.get("image") or [] if m.get("url")), None)
    image = clean_str(p.get("imageLink")) or (s.api_url + quote(media) if media else None)
    return ScrapedEvent(
        source=PetahTikvaMuniScraper.name,
        source_event_id=str(item.get("id")),
        url=s.site_url + path,
        title=title,
        description=html_to_text((p.get("brief") or {}).get("markup")) or clean_str(p.get("ogdescription")),
        start_at=start,
        end_at=end,
        time_known=True,
        venue_name=venue,
        address=address,
        city=s.city,
        source_genres=_genres(p.get("categories"), s.genre_groups),
        organizer=f"עיריית {s.city}",
        image_url=image if image and image.startswith("http") else None,
        min_price=price,
        max_price=price,
        tickets=tiers,
        extra={"purchase_url": link} if link and link.startswith("http") else {},
    )
