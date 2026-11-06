"""Fever (feverup.com) in Israel - Candlelight concerts (classical and pop tributes by candlelight) at the
Tel Aviv Museum of Art, the Eretz Israel Museum, the Jerusalem Theatre and Haifa's Rappaport Center.

Each city page (``/en/<city>-israel``) links its plans (``/m/<id>``, ~60 in all). A plan's page has a
schema.org ``Event`` JSON-LD only when it is a dated show: its next session (start, end, place with
coordinates, image, ticket offers in ILS). Plans without one (guided tours, games, rentals, undated
experiences) are not events and are skipped. The JSON-LD date is only the next session with tickets
left; the page's ticket selector holds every session, one item per zone with ``starts_at_iso``,
``ends_at_iso``, its price and ``has_available_tickets``: one event per session start, priced by its
zones and sold out when none has tickets. 3 + ~60 requests a run. robots.txt allows /m/ pages.
"""

from __future__ import annotations

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
# One ticket-selector item (a zone of a session); no nested objects between these keys.
_SESSION = re.compile(
    r'"label":"([^"]*)",[^{}]*?"starts_at_iso":"([^"]+)","ends_at_iso":"([^"]*)"'
    r'[^{}]*?"has_available_tickets":(true|false)[^{}]*?"price":([\d.]+)'
)


class FeverSettings(SourceSettings):
    site_url: str = "https://feverup.com"
    city_paths: list[str] = Field(
        default_factory=lambda: ["/en/tel-aviv-israel", "/en/jerusalem-israel", "/en/haifa-israel"]
    )
    plan_path: str = "/m/{id}/en"


class FeverScraper(BaseScraper[FeverSettings]):
    name = "fever"
    settings_model = FeverSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        plans: dict[str, None] = {}
        for path in s.city_paths:
            page = await self.optional(f"city {path}", self.get_text(s.site_url + path))
            plans.update(dict.fromkeys(_PLAN.findall(page or "")))
        self.log.debug("listed %d plans", len(plans))

        async def read(plan_id: str) -> list[ScrapedEvent]:
            url = s.site_url + s.plan_path.format(id=plan_id)
            events = parse_plan(await self.get_text(url), plan_id, url, self.timezone)
            return [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]

        async for events in self.stream_isolated(list(plans), read, lambda p: f"plan {p}"):
            for event in events:
                yield event


def parse_plan(page_html: str, plan_id: str, url: str, timezone: str) -> list[ScrapedEvent]:
    """A plan's page -> one event per session; [] for a plan with no dated Event (a tour, a game)."""
    event = parse_jsonld(page_html, plan_id, url, timezone)
    if event is None:
        return []
    sessions = parse_sessions(page_html, timezone)
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
