"""Eventer (eventer.co.il).

Listing: every event the site lists. ``/sliders/list.js`` describes the home page: its
"categories" slider names the categories (``/sliders/categories/<category>/events.js``)
and every other slider is read with ``/sliders/<linkName>/byLink.js``. Each returns its
complete slide list in one response. Only event slides are kept (sliders also hold
venues, artists, articles). ``categories`` from the config are always read as well, and
so is every event of each producer in ``users`` (``/user/<linkName>/getData``, the profile
page a producer embeds on its own site; e.g. toMix, whose shows are mostly not on sliders).
Details: ``/events/explainNames/<linkName>.js`` (event + JSON-LD) and
``/events/<id>/ticketTypes.js`` (all ticket tiers incl. free registrations).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    parse_iso,
    to_decimal,
    to_float,
    to_int,
)
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings

UNLIMITED = -1
CATEGORIES_SLIDER = 6  # sliderType of the home page's category tiles
EVENT_SLIDE = 5  # slideType of an event (1 artist, 2 venue, 3 production, 8 article...)


class EventerSettings(SourceSettings):
    base_url: str
    categories: list[str] = Field(default_factory=list)
    list_path: str
    # Read every category and slider the home page shows, not only `categories`.
    discover: bool = True
    sliders_path: str = "/sliders/list.js"
    slider_path: str = "/sliders/{link_name}/byLink.js"
    # Producer profiles (user linkNames) whose whole event list is read on top of the sliders.
    users: list[str] = Field(default_factory=list)
    user_events_path: str = "/user/{user}/getData"
    event_details_path: str
    ticket_types_path: str
    # The site's artist catalog (one request) and each artist's profile (social links). Empty = skip.
    artists_path: str = ""
    artist_path: str = "/Artist/{link_name}/byLink.js"
    artist_page_path: str = "/artists/{link_name}"
    # New artists whose profile is fetched per run; the rest are saved from the catalog alone.
    max_new_artists: int = Field(40, ge=0)


class EventerScraper(BaseScraper[EventerSettings]):
    name = "eventer"
    settings_model = EventerSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        link_names = await self._list_link_names()
        self.log.info("listed %d events", len(link_names))
        if self.settings.artists_path:
            self.artist_profiles = await self.optional("artists", self._list_artists()) or []
            self.log.info("listed %d artists", len(self.artist_profiles))
        async for event in self.stream_isolated(link_names, self._fetch_event, label=lambda n: n):
            yield event

    async def _list_link_names(self) -> list[str]:
        s = self.settings
        categories, sliders = list(s.categories), []
        if s.discover:
            for slider in await self.optional("slider list", self.get_json(s.base_url + s.sliders_path, expect=list)) or []:
                if not isinstance(slider, dict):
                    continue
                if slider.get("sliderType") == CATEGORIES_SLIDER:
                    categories += [str(c["linkName"]) for c in slider.get("slides") or [] if isinstance(c, dict) and c.get("linkName")]
                elif slider.get("linkName"):
                    sliders.append(str(slider["linkName"]))
        listings = [(f"category {c}", s.base_url + s.list_path.format(category=c), dict) for c in dict.fromkeys(categories)]
        listings += [(f"slider {n}", s.base_url + s.slider_path.format(link_name=n), list) for n in dict.fromkeys(sliders)]
        # A profile set to open one event answers with that event's path ("/rc82f") instead.
        listings += [(f"user {u}", s.base_url + s.user_events_path.format(user=u), dict | str) for u in dict.fromkeys(s.users)]

        seen: dict[str, None] = {}
        for label, url, shape in listings:
            try:  # one renamed/broken listing must not hide the others
                payload = await self.get_json(url, expect=shape)
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.record_failure(label, exc)
                continue
            if isinstance(payload, str):
                if link := payload.strip("/"):
                    seen[link] = None
                continue
            if isinstance(payload, list):  # byLink.js: [slider]
                payload = payload[0] if payload and isinstance(payload[0], dict) else {}
            seen.update(
                (str(slide["linkName"]), None)
                for slide in payload.get("slides") or payload.get("events") or []  # user getData: events
                if isinstance(slide, dict) and slide.get("linkName") and slide.get("slideType", EVENT_SLIDE) == EVENT_SLIDE
            )
        if not seen and self.failures:
            raise ParseError("every listing failed")
        return list(seen)

    async def _fetch_event(self, link_name: str) -> ScrapedEvent:
        s = self.settings
        details = await self.get_json(s.base_url + s.event_details_path.format(link_name=link_name), expect=dict)
        event = details.get("event")
        event_id = event.get("_id") if isinstance(event, dict) else None
        if not event_id:
            raise ParseError("event id missing")
        # Ticket endpoint down -> still save the event, without tiers.
        tickets_payload = await self.optional(
            f"{link_name} tickets", self.get_json(s.base_url + s.ticket_types_path.format(event_id=event_id), expect=dict)
        ) or {}
        return parse_event(details, tickets_payload, f"{s.base_url}/{link_name}", self.timezone)


    async def _list_artists(self) -> list[ArtistProfile]:
        s = self.settings
        catalog = await self.get_json(s.base_url + s.artists_path, expect=dict)
        slides = [a for a in catalog.get("slides") or [] if isinstance(a, dict) and a.get("linkName") and a.get("title")]
        new = [a for a in slides if not set(map(str.casefold, artist_names(a["title"]))) <= self.known_artists.keys()]
        details: dict[str, dict[str, Any]] = {}
        for slide in new[: s.max_new_artists]:
            link = str(slide["linkName"])
            path = s.artist_path.format(link_name=quote(link))
            details[link] = await self.optional(f"artist {link}", self.get_json(s.base_url + path, expect=dict)) or {}
        return [p for a in slides for p in parse_artist(a, details.get(str(a["linkName"])) or {}, s.base_url + s.artist_page_path)]


def artist_names(title: str) -> list[str]:
    """"בוריס ברצ'ה | Boris Brejcha" -> both spellings, so either one is spotted in event text."""
    return [n for part in title.split("|") if (n := clean_str(part))]


def parse_artist(slide: dict[str, Any], details: dict[str, Any], page_template: str) -> list[ArtistProfile]:
    images = details.get("images") or slide.get("images") or {}
    links = [
        u for key in ("instagramLink", "youtubeLink", "soundcloudLink", "facebookLink", "webSiteLink", "wikipediaLink")
        if isinstance(u := details.get(key), str) and u.startswith("http")
    ]
    return [
        ArtistProfile(
            name=name,
            source=EventerScraper.name,
            image_url=clean_str(images.get("imageSquare") or images.get("imageDefault")),
            profile_url=page_template.format(link_name=quote(str(slide["linkName"]))),
            links=links,
        )
        for name in artist_names(str(slide.get("title")))
    ]


def parse_event(details: dict[str, Any], tickets_payload: dict[str, Any], page_url: str, default_tz: str) -> ScrapedEvent:
    event = details.get("event") or {}
    if not event.get("_id"):
        raise ParseError("event missing")

    ld = details.get("jsonLdData") or {}
    location = event.get("location") or {}
    schedule = event.get("schedule") or {}
    tz = location.get("timezone") or default_tz
    currency = event.get("defaultCurrency") or "ILS"
    categories = event.get("eventCategories") or {}
    platform = event.get("ticketPlatform") or {}
    location_description = clean_str(event.get("locationDescription"))

    tickets = [_parse_ticket(t, currency) for t in tickets_payload.get("ticketTypes") or [] if isinstance(t, dict)]
    priced = [t.price for t in tickets if t.price is not None and t.is_active]

    return ScrapedEvent(
        source=EventerScraper.name,
        source_event_id=str(event["_id"]),
        url=page_url,
        title=clean_str(event.get("name")) or clean_str(platform.get("name")) or "",
        description=html_to_text(event.get("eventDesc")),
        start_at=parse_iso(ld.get("startDate")) or parse_iso(schedule.get("start"), tz),
        end_at=parse_iso(ld.get("endDate")) or parse_iso(schedule.get("end"), tz),
        venue_name=location_description.split(",")[0].strip() if location_description else None,
        address=location_description,
        city=clean_str(location.get("addressLocality")),
        latitude=to_float(location.get("latitude")),
        longitude=to_float(location.get("longitude")),
        event_types=[
            c for c in [categories.get("topEventCategoryString"), *(categories.get("subEventCategoryStrings") or [])] if c
        ],
        min_age=to_int(event.get("ageLimit")),
        organizer=clean_str((event.get("producer") or {}).get("publicName")),
        images=_images(event),
        currency=currency,
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=tickets_payload.get("totalRemaining") == 0,
        tickets=tickets,
        extra={
            "link_name": event.get("linkName"),
            "status": event.get("status"),
            "open_doors": schedule.get("openDoors"),
            "allow_multiple_tickets": tickets_payload.get("allowMultipleTickets"),
        },
    )


def _parse_ticket(ticket: dict[str, Any], currency: str) -> TicketTier:
    price = to_decimal(ticket.get("price"))
    closed = price is not None and price < 0  # -1 = tier not on sale (originalPrice keeps the face value)
    price = None if closed else price
    remaining = to_int(ticket.get("remaining"))
    remaining = None if remaining in (None, UNLIMITED) else remaining
    return TicketTier(
        source_ticket_id=clean_str(ticket.get("_id")),
        name=clean_str(ticket.get("name")) or "Ticket",
        description=clean_str(ticket.get("description")),
        price=price,
        original_price=to_decimal(ticket.get("originalPrice")),
        currency=currency,
        is_free=price == 0,
        is_active=not (closed or ticket.get("isPurchaseWithCouponOnly", False)),
        is_sold_out=remaining == 0,
        remaining=remaining,
        sale_end=parse_iso(ticket.get("toDate")),
    )


def _images(event: dict[str, Any]) -> list[str]:
    """Marketing artwork first (default/wide/square/mobile), then page photos."""
    platform_images = (event.get("ticketPlatform") or {}).get("images") or {}
    candidates = [
        *(platform_images.get(k) for k in ("imageDefault", "imageWide", "imageSquare", "imageMobile")),
        *(event.get(k) for k in ("fbPhoto", "mobilePhoto", "thumbnail", "background", "mobileBackground")),
    ]
    return [c for c in candidates if isinstance(c, str) and c.startswith("http")]
