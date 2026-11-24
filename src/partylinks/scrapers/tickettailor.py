"""Ticket Tailor (tickettailor.com) - a self-service box office used by organizers in Israel and abroad
(stand-up tours, community events, Jewish life in the USA).

The site has no catalogue: no events sitemap, no API without the organizer's key, and ``/discover`` is a
hand-picked page of ~20 events. Each organizer has a box office, ``/events/<organizer>``, listing its event
pages ``/events/<organizer>/<id>``. The organizers read are the configured ones plus every one named in
another source's ticket link or in this source's own stored pages (``linked_urls``: Ozen, Ogo, Ogo USA link
there), so a new organizer is picked up the run after any source links it. Each event page carries a
schema.org ``Event`` per date (a series page has several): start / end with the offset, the place
("venue | street, city, country") with its ISO country, artwork and offers (price, currency). Israel and
abroad are both kept; an event abroad gets its country and the venue's zone. An empty offer list on a page
titled "Sold out" / "Join Waiting List" is a sold-out date. Requests: one per organizer, one per event page.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import time
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, parse_iso, to_decimal
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ORGANIZER = re.compile(r"(?:tickettailor\.com|buytickets\.at)/(?:events/)?([A-Za-z0-9_-]+)(?=[/\"'\s<>?#]|$)")
_TITLE = re.compile(r"<title>([^<]*)</title>", re.IGNORECASE)
_OCCURRENCE = re.compile(r"#event-(\d+)$")
_POSTAL = re.compile(r"\b[A-Z]{0,2}\d[\dA-Z -]*\b")


class TicketTailorSettings(SourceSettings):
    site_url: str = "https://www.tickettailor.com"
    box_office_path: str = "/events/{organizer}"
    # Organizers read every run; others join from linked_urls.
    organizers: list[str] = Field(default_factory=list)
    # Path words that are no organizer (tickettailor.com/<word>, buytickets.at/<word>).
    not_organizers: list[str] = Field(
        default_factory=lambda: ["checkout", "events", "discover", "promoter", "blog", "help", "login", "sign-up"]
    )
    sold_out_titles: list[str] = Field(default_factory=lambda: ["sold out", "join waiting list"])
    home_country: str = "IL"
    # An organizer whose places name no town ("Kii Hub in LES"): its town.
    organizer_cities: dict[str, str] = Field(default_factory=dict)


def organizers(texts: list[str], s: TicketTailorSettings) -> list[str]:
    """The configured organizers, then every one named in ``texts`` (links), once each, in order."""
    named = [m.lower() for t in texts for m in _ORGANIZER.findall(t)]
    skip = {w.lower() for w in s.not_organizers}
    return list(dict.fromkeys(o for o in [*(x.lower() for x in s.organizers), *named] if o not in skip))


def event_links(box_office_html: str, organizer: str, s: TicketTailorSettings) -> list[str]:
    """The box office's event pages, in its own order (soonest first), once each."""
    page = html.unescape(box_office_html)
    ids = re.findall(rf"/events/{re.escape(organizer)}/(\d+)(?=[\"'/?#])", page, re.IGNORECASE)
    return [f"{s.site_url}/events/{organizer}/{i}" for i in dict.fromkeys(ids)]


def _place(location: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """(venue, address, city, ISO country) of a ``Place``: its name is "venue | street, city, country"."""
    if not isinstance(location, dict):
        return None, None, None, None
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    country = clean_str(address.get("addressCountry"))
    text = clean_str(address.get("streetAddress")) or clean_str(location.get("name")) or ""
    pieces = [p.strip() for p in text.split("|") if p.strip()]
    venue = pieces[0] if len(pieces) > 1 else None
    street = pieces[-1] if pieces else None
    parts = [p.strip() for p in (street or "").split(",") if p.strip()]
    city = None
    locality = clean_str(address.get("postalCode"))
    if len(parts) == 2 and len(parts[1]) == 2 and parts[1].isupper():  # "Los Angeles, CA"
        city = parts[0]
    elif locality and not any(c.isdigit() for c in locality):  # the town typed as the postcode ("Aventura")
        city = locality
    elif len(parts) >= 2:
        # "..., Tel Aviv-Yafo, Israel" / "..., 60316 Frankfurt am Main, Germany" / "..., 2230 Luxembourg"
        candidates = parts[-2::-1] if len(parts) >= 3 else [parts[-1]]
        for candidate in candidates:
            name = clean_str(_POSTAL.sub("", candidate))
            if name and not (len(name) <= 3 and name.isupper()):  # "NY 10001": a state, look one further
                city = name
                break
    return venue or (street.split(",")[0] if street else None), street, city, country


def parse_page(page_html: str, url: str, organizer: str, s: TicketTailorSettings, timezone: str) -> list[ScrapedEvent]:
    """Every dated ``Event`` of one event page (a series page has one per date)."""
    title_tag = _TITLE.search(page_html)
    page_title = html.unescape(title_tag.group(1)).strip().lower() if title_tag else ""
    sold_out_page = any(page_title.startswith(w) for w in s.sold_out_titles)
    nodes = jsonld_nodes(page_html)
    organizer_name = next(
        (
            clean_str(n.get("name"))
            for n in nodes
            if n.get("@type") == "Organization" and "#organizer" in str(n.get("@id"))
        ),
        None,
    )
    events = []
    for node in nodes:
        if node.get("@type") != "Event":
            continue
        name = clean_str(html.unescape(str(node.get("name") or "")))
        start = parse_iso(node.get("startDate"), timezone)
        if not name or start is None:
            continue
        end = parse_iso(node.get("endDate"), timezone)
        if end is not None and end <= start:  # "00:00" of the same day: no end given
            end = None
        venue, address, city, country = _place(node.get("location"))
        city = city or s.organizer_cities.get(organizer)
        abroad = bool(country) and country != s.home_country
        zone_name = local_zone(country, timezone, city) if abroad else None
        occurrence = _OCCURRENCE.search(str(node.get("@id") or ""))
        offers = [o for o in node.get("offers") or [] if isinstance(o, dict)]
        tiers = [
            TicketTier(
                name=clean_str(o.get("name")) or "Ticket",
                price=to_decimal(o.get("price")),
                currency=clean_str(o.get("priceCurrency")) or "ILS",
                is_free=to_decimal(o.get("price")) == 0,
            )
            for o in offers
        ]
        prices = [t.price for t in tiers if t.price is not None]
        currency = next((t.currency for t in tiers), None)
        images = [str(i) for i in node.get("image") or [] if isinstance(i, str) and i.startswith("http")]
        events.append(
            ScrapedEvent(
                source=TicketTailorScraper.name,
                source_event_id=occurrence.group(1) if occurrence else f"{url.rsplit('/', 1)[-1]}-{start.isoformat()}",
                url=url,
                title=name,
                description=html_to_text(clean_str(node.get("description"))),
                start_at=start,
                end_at=end,
                time_known=start.timetz().replace(tzinfo=None) != time(0, 0),
                venue_name=venue,
                address=address,
                city=city,
                country=country if abroad else None,
                time_zone=zone_name,
                images=images[:1],
                tickets=tiers,
                currency=currency or "ILS",
                min_price=min(prices) if prices else None,
                max_price=max(prices) if prices else None,
                is_sold_out=not offers and sold_out_page,
                organizer=organizer_name or organizer,
            )
        )
    return events


class TicketTailorScraper(BaseScraper[TicketTailorSettings]):
    name = "tickettailor"
    settings_model = TicketTailorSettings
    link_hosts = ("tickettailor.com", "buytickets.at")

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        pages: list[tuple[str, str]] = []
        for organizer in organizers(self.linked_urls, s):
            box_office = await self.optional(
                f"box office {organizer}", self.get_text(s.site_url + s.box_office_path.format(organizer=organizer))
            )
            if box_office:
                pages += [(organizer, link) for link in event_links(box_office, organizer, s)]
        found: list[ScrapedEvent] = []

        async def read(item: tuple[str, str]) -> list[ScrapedEvent]:
            organizer, link = item
            return parse_page(await self.get_text(link), link, organizer, s, self.timezone)

        async for batch in self.stream_isolated(pages, read, lambda item: item[1]):
            found += [
                e
                for e in batch
                if is_current(e.start_at, e.end_at, retention, e.time_zone or self.timezone, time_known=e.time_known)
            ]
        # Every page is read before the first is yielded: a box office's order is per organizer, not overall.
        for event in sorted(found, key=lambda e: e.start_at.timestamp() if e.start_at else float("inf")):
            yield event
