"""Fever (feverup.com) in Israel - Candlelight concerts (classical and pop tributes by candlelight) at the
Tel Aviv Museum of Art, the Eretz Israel Museum, the Jerusalem Theatre, Haifa's Rappaport Center and the
Netanya / Ashdod halls, plus the party nights Fever sells itself (MITZI in Beer Sheva and Pardes Hanna).

The Israeli city pages (``/en/<city>-israel``: the configured ones plus every ``-israel`` city they link)
and each city's visible categories (its own taxonomy: a ``slug`` with ``is_hidden: false`` and no
redirection, e.g. ``/en/tel-aviv-israel/nightlife-clubs``) link the plans (``/m/<id>``); a category page
lists plans its city page leaves out. A plan's page has a schema.org ``Event`` JSON-LD when it is a dated
show: its next session (start, end, place with coordinates, image, ticket offers in ILS). Plans whose
``category`` is "mix" are resold inventory (tours, rentals, tastings, the timed-entry slots of Lucid Dreams)
and skipped. A plan with a ``Product`` JSON-LD is an event when it is no gift card and its sessions are
dated (under a day: a month-long open ticket is a game): its place is the plan's ``defaultPlace``. Waitlists have no sessions.
The JSON-LD date is only the next session with tickets left; the page's ticket selector holds every
session, one item per zone with ``starts_at_iso``, ``ends_at_iso``, its price and ``has_available_tickets``:
one event per session start, priced by its zones and sold out when none has tickets.
~6 city + ~45 category + ~85 plan requests a run. robots.txt allows /m/ pages.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, jsonld_nodes, parse_iso, to_decimal, to_float
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PLAN = re.compile(r"/m/(\d+)")
_CITY = re.compile(r"/en/([a-z-]+-israel)\b")
# A category of the city's taxonomy; hidden or redirected ones are no page of their own.
_CATEGORY = re.compile(r'"slug":"([a-z0-9-]+)","slug_translations":')
_CATEGORY_STATE = re.compile(r'"is_hidden":(true|false),"id_for_redirection":(null|\d+)')
_PLAN_CATEGORY = re.compile(r'"immersive-plan-detail:\d+[^"]*":\{[^{}]*?"category":"([^"]*)"')
_PLACE = re.compile(
    r'"defaultPlace":\{"address":"((?:[^"\\]|\\.)*)",[^{}]*?"latitude":(-?[\d.]+),[^{}]*?'
    r'"longitude":(-?[\d.]+),[^{}]*?"name":"((?:[^"\\]|\\.)*)"'
)
_DAY_SECONDS = 24 * 3600
# One ticket-selector item (a zone of a session); no nested objects between these keys.
_SESSION = re.compile(
    r'"label":"([^"]*)",[^{}]*?"starts_at_iso":"([^"]+)","ends_at_iso":"([^"]*)"'
    r'[^{}]*?"has_available_tickets":(true|false)[^{}]*?"price":([\d.]+)'
)


class FeverSettings(SourceSettings):
    site_url: str = "https://feverup.com"
    city_paths: list[str] = Field(
        default_factory=lambda: [
            "/en/tel-aviv-israel",
            "/en/jerusalem-israel",
            "/en/haifa-israel",
            "/en/beer-sheva-israel",
            "/en/netanya-israel",
            "/en/ashdod-israel",
        ]
    )
    plan_path: str = "/m/{id}/en"


class FeverScraper(BaseScraper[FeverSettings]):
    name = "fever"
    settings_model = FeverSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        plans: dict[str, list[str]] = {}  # plan id -> the categories listing it
        cities = list(dict.fromkeys(s.city_paths))
        categories: list[str] = []
        for path in cities:  # grows with the Israeli cities the pages link
            page = await self.optional(f"city {path}", self.get_text(s.site_url + path)) or ""
            for plan_id in _PLAN.findall(page):
                plans.setdefault(plan_id, [])
            cities.extend(c for c in dict.fromkeys(f"/en/{slug}" for slug in _CITY.findall(page)) if c not in cities)
            categories.extend(c for c in (f"{path}/{slug}" for slug in visible_categories(page)) if c not in categories)
        for path in categories:
            page = await self.optional(f"category {path}", self.get_text(s.site_url + path)) or ""
            label = path.rsplit("/", 1)[-1].replace("-", " ")
            for plan_id in _PLAN.findall(page):
                if label not in (listed := plans.setdefault(plan_id, [])):
                    listed.append(label)
        self.log.debug("listed %d plans from %d cities, %d categories", len(plans), len(cities), len(categories))

        async def read(plan_id: str) -> list[ScrapedEvent]:
            url = s.site_url + s.plan_path.format(id=plan_id)
            events = parse_plan(await self.get_text(url), plan_id, url, self.timezone, plans[plan_id])
            return [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]

        async for events in self.stream_isolated(list(plans), read, lambda p: f"plan {p}"):
            for event in events:
                yield event


def visible_categories(page_html: str) -> list[str]:
    """A city page's taxonomy -> the slugs of its visible categories (not hidden, not redirected)."""
    slugs: list[str] = []
    for match in _CATEGORY.finditer(page_html):
        state = _CATEGORY_STATE.search(page_html, match.end(), match.end() + 5000)
        if state and state.groups() == ("false", "null") and match.group(1) not in slugs:
            slugs.append(match.group(1))
    return slugs


def parse_plan(
    page_html: str, plan_id: str, url: str, timezone: str, listed_in: list[str] | None = None
) -> list[ScrapedEvent]:
    """A plan's page -> one event per session; [] for a plan with no dated Event (a tour, a game)."""
    if (category := _PLAN_CATEGORY.search(page_html)) and category.group(1) == "mix":
        return []  # resold inventory: tours, rentals, tastings, timed-entry slots (Lucid Dreams every 30 min)
    sessions = parse_sessions(page_html, timezone)
    event = parse_jsonld(page_html, plan_id, url, timezone)
    if event is None:
        event = parse_product(page_html, plan_id, url, listed_in or [])
        # Only dated sessions: a month-long validity window is an open ticket, not a performance.
        sessions = {
            start: session
            for start, session in sessions.items()
            if session[0] is not None and 0 < (session[0] - start).total_seconds() <= _DAY_SECONDS
        }
        if event is None or not sessions:
            return []
    if not sessions:  # no ticket selector: the JSON-LD's next session alone
        return [event]
    return [
        event.model_copy(
            update={
                "source_event_id": f"{plan_id}:{start.isoformat()}",
                "start_at": start,
                "end_at": end if end and end > start else None,
                "tickets": tiers,
                "min_price": min((t.price for t in tiers if t.price is not None), default=None),
                "max_price": max((t.price for t in tiers if t.price is not None), default=None),
                "is_sold_out": sold_out,
            },
            deep=True,
        )
        for start, (end, tiers, sold_out) in sorted(sessions.items())
    ]


def parse_sessions(page_html: str, timezone: str) -> dict[Any, tuple[Any, list[TicketTier], bool]]:
    """The ticket selector's zones grouped by session start -> (end, a tier per zone, sold out)."""
    sessions: dict[Any, tuple[Any, list[TicketTier], bool]] = {}
    for label, starts, ends, available, price in _SESSION.findall(page_html):
        if (start := parse_iso(starts, timezone)) is None:
            continue
        end, tiers, sold_out = sessions.get(start, (parse_iso(ends, timezone), [], True))
        name = clean_str(label) or "כרטיס"
        if all(t.name != name for t in tiers):  # the selector repeats its items
            tiers.append(TicketTier(name=name, price=to_decimal(price)))
        sessions[start] = (end, tiers, sold_out and available == "false")
    return sessions


def parse_product(page_html: str, plan_id: str, url: str, listed_in: list[str]) -> ScrapedEvent | None:
    """A plan Fever sells itself under a Product JSON-LD (a party night), undated (the ticket selector
    dates it): its place is the plan's ``defaultPlace``, its genres the categories listing it. None for
    gift cards."""
    node = next((n for n in jsonld_nodes(page_html) if n.get("@type") == "Product"), None)
    if node is None or not (title := clean_str(node.get("name"))) or "gift card" in title.lower():
        return None
    if _PLAN_CATEGORY.search(page_html) is None:  # not a plan page Fever renders itself
        return None
    venue = city = None
    lat = lng = None
    if place := _PLACE.search(page_html):
        address, lat_text, lng_text, name = (_unescape(g) for g in place.groups())
        venue = clean_str(name)
        lat, lng = to_float(lat_text), to_float(lng_text)
        parts = [p.strip() for p in address.split(",") if p.strip() and not p.strip().isdigit()]
        city = parts[-1] if parts else None
    image = node.get("image")
    image = image.get("contentUrl") if isinstance(image, dict) else image
    return ScrapedEvent(
        source=FeverScraper.name,
        source_event_id=plan_id,
        url=url,
        title=title,
        description=clean_str(node.get("description")),
        venue_name=venue,
        city=city,
        latitude=lat if lat is not None and lng is not None else None,
        longitude=lng if lat is not None and lng is not None else None,
        source_genres=listed_in or ["experience"],
        images=[image] if isinstance(image, str) and image.startswith("http") else [],
        extra={"purchase_url": url},
    )


def _unescape(text: str) -> str:
    """A JSON string's body as the page writes it (``\\u002F``, ``\\"``) -> its text."""
    try:
        return str(json.loads(f'"{text}"'))
    except ValueError:
        return text


def parse_jsonld(page_html: str, plan_id: str, url: str, timezone: str) -> ScrapedEvent | None:
    """A plan's Event JSON-LD: place, image, offers and its next session; None when undated."""
    node = next((n for n in jsonld_nodes(page_html) if "Event" in str(n.get("@type"))), None)
    if node is None or not (title := clean_str(node.get("name"))):
        return None
    start_at = parse_iso(node.get("startDate"), timezone)
    if start_at is None:
        return None
    end_at = parse_iso(node.get("endDate"), timezone)
    place: dict[str, Any] = node.get("location") if isinstance(node.get("location"), dict) else {}
    geo = place.get("geo") or {}
    lat, lng = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    address = place.get("address") or {}
    offers = node.get("offers") if isinstance(node.get("offers"), list) else [node.get("offers") or {}]
    tiers = [
        TicketTier(name=clean_str(o.get("name")) or "כרטיס", price=price)
        for o in offers
        if isinstance(o, dict) and (price := to_decimal(o.get("price"))) is not None
    ]
    prices = sorted(t.price for t in tiers if t.price is not None)
    image = node.get("image")
    image = image[0] if isinstance(image, list) and image else image
    candlelight = title.lower().startswith("candlelight")
    return ScrapedEvent(
        source=FeverScraper.name,
        source_event_id=f"{plan_id}:{start_at.isoformat()}",
        url=url,
        title=title,
        description=clean_str(node.get("description")),
        start_at=start_at,
        end_at=end_at if end_at and end_at > start_at else None,
        venue_name=clean_str(place.get("name")),
        city=clean_str(address.get("addressLocality")) if isinstance(address, dict) else None,
        latitude=lat if lat is not None and lng is not None else None,
        longitude=lng if lat is not None and lng is not None else None,
        source_genres=["candlelight", "classical"] if candlelight else ["experience"],
        images=[image] if isinstance(image, str) and image.startswith("http") else [],
        tickets=tiers,
        min_price=prices[0] if prices else None,
        max_price=prices[-1] if prices else None,
        is_sold_out=bool(offers)
        and all("InStock" not in str(o.get("availability")) for o in offers if isinstance(o, dict)),
        extra={"purchase_url": url},
    )
