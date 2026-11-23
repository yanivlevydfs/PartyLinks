"""Meetup (meetup.com) - community meetups: tech talks, developer groups, startup and social events.

robots.txt only blocks feeds. GraphQL (``POST /gql2``, no login), in three passes, each event kept once:

1. Groups: ``groupSearch`` near Tel Aviv (150 km covers Israel) for each of the site's topic
   categories (read from ``topicCategories``, 20 in Nov 2026), each group with its upcoming events.
   The search stops at 200 groups per query (only Technology reaches it); the groups past it with an
   event in the next weeks come in through pass 2.
2. Events: ``recommendedEvents`` near the point and a few smaller ones (``centers``: Haifa,
   Beersheba, Jerusalem; it ranks the centre's events first), in-person only, every date of a
   series (``doConsolidateEvents: false``), window by window (``window_days`` up to ``days_ahead``):
   the search only reaches ~45 days ahead, whatever the range.
3. Each group with more upcoming events than one page (weekly series a year ahead), or met only in
   pass 2: its whole upcoming list (``groupByUrlname``), cursor-paged to the end.

Only venues in Israel are kept (the radius reaches Jordan and Lebanon); events with no venue (place
sent to members) or online are dropped. Each event gives title, start/end, markdown description,
venue (name, address, city, coordinates), group, its group's topic category ("technology" marks a
hi-tech event) and the fee, if any. Nov 2026: 60 events from the event search alone; ~250 so.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

EVENT = """fragment E on Event {
  id title dateTime endTime eventUrl eventType status description
  venue { name address city lat lon country }
  group { urlname name topicCategory { urlkey name } }
  featuredEventPhoto { highResUrl }
  feeSettings { amount currency }
}"""

CATEGORIES = "{ topicCategories { edges { node { id urlkey } } } }"

GROUPS = (
    """query($lat: Float!, $lon: Float!, $radius: Float, $category: ID, $first: Int, $after: String) {
  groupSearch(filter: {query: "*", lat: $lat, lon: $lon, radius: $radius, topicCategoryId: $category},
              first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges { node { urlname country
      events(first: 50, status: ACTIVE) { pageInfo { hasNextPage } edges { node { ...E } } } } }
  }
}
"""
    + EVENT
)

SEARCH = (
    """query($lat: Float!, $lon: Float!, $radius: Float, $first: Int, $after: String, $start: String,
       $end: String) {
  recommendedEvents(filter: {lat: $lat, lon: $lon, eventType: PHYSICAL, radius: $radius,
                             startDateRange: $start, endDateRange: $end, doConsolidateEvents: false},
                    first: $first, after: $after, sort: {sortField: DATETIME}) {
    pageInfo { hasNextPage endCursor }
    edges { node { ...E } }
  }
}
"""
    + EVENT
)

GROUP = (
    """query($urlname: String!, $after: String) {
  groupByUrlname(urlname: $urlname) {
    events(first: 50, after: $after, status: ACTIVE) { pageInfo { hasNextPage endCursor } edges { node { ...E } } }
  }
}
"""
    + EVENT
)


class Center(BaseModel):
    latitude: float
    longitude: float
    radius_km: float = Field(gt=0)


class MeetupSettings(SourceSettings):
    graphql_url: str
    latitude: float
    longitude: float
    radius_km: float = Field(150, gt=0)
    # More points, each with its own radius, asked after the main one by the event search.
    centers: list[Center] = Field(default_factory=list)
    window_days: int = Field(30, ge=1)
    days_ahead: int = Field(120, ge=1)
    page_size: int = Field(50, ge=1, le=50)


class MeetupScraper(BaseScraper[MeetupSettings]):
    name = "meetup"
    settings_model = MeetupSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s, seen = self.settings, set[str]()
        done, todo = set[str](), list[str]()  # groups whose whole upcoming list was read / still to read
        for category in await self._categories():
            async for node in self._pages(GROUPS, "groupSearch", lat=s.latitude, lon=s.longitude,
                                          radius=s.radius_km, category=category):  # fmt: skip
                if (clean_str(node.get("country")) or "").lower() != "il" or not node.get("urlname"):
                    continue
                events = node.get("events") or {}
                for edge in events.get("edges") or []:
                    if event := self._event(edge.get("node") or {}, seen):
                        yield event
                if (events.get("pageInfo") or {}).get("hasNextPage"):
                    todo.append(node["urlname"])
                else:
                    done.add(node["urlname"])
        points = [Center(latitude=s.latitude, longitude=s.longitude, radius_km=s.radius_km), *s.centers]
        start = datetime.now(zone(self.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
        for offset in range(0, s.days_ahead, s.window_days):
            begin = start + timedelta(days=offset)
            for point in points:
                async for node in self._pages(SEARCH, "recommendedEvents", lat=point.latitude, lon=point.longitude,
                                              radius=point.radius_km, start=begin.isoformat(),
                                              end=(begin + timedelta(days=s.window_days)).isoformat()):  # fmt: skip
                    if event := self._event(node, seen):
                        yield event
                        group = clean_str((node.get("group") or {}).get("urlname"))
                        if group and group not in done and group not in todo:
                            todo.append(group)
        for group in todo:
            async for node in self._pages(GROUP, "groupByUrlname", urlname=group):
                if event := self._event(node, seen):
                    yield event

    async def _categories(self) -> list[str]:
        body = await self._ask(CATEGORIES, {})
        ids = [
            str(e["node"]["id"])
            for e in ((body.get("topicCategories") or {}).get("edges") or [])
            if (e.get("node") or {}).get("id")
        ]
        if not ids:
            raise ParseError("meetup: no topic categories")
        return ids

    async def _pages(self, query: str, field: str, **variables: Any) -> AsyncIterator[dict[str, Any]]:
        """Every node of a cursor-paged connection, to its last page."""
        cursor, cursors = None, set[str]()
        while True:
            data = await self._ask(query, {**variables, "first": self.settings.page_size, "after": cursor})
            page = data.get(field) or {}
            if field == "groupByUrlname":
                page = page.get("events") or {}
            for edge in page.get("edges") or []:
                yield edge.get("node") or {}
            info = page.get("pageInfo") or {}
            cursor = info.get("endCursor")
            if not info.get("hasNextPage") or not cursor or cursor in cursors:
                return
            cursors.add(cursor)

    async def _ask(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = await self.post_json(
            self.settings.graphql_url, expect=dict, json={"query": query, "variables": variables}
        )
        if body.get("errors"):
            raise ParseError(f"meetup graphql: {str(body['errors'])[:300]}")
        return body.get("data") or {}

    def _event(self, node: dict[str, Any], seen: set[str]) -> ScrapedEvent | None:
        if not node.get("id") or node["id"] in seen:
            return None
        seen.add(node["id"])
        return parse_event(node, self.timezone)


def parse_event(node: dict[str, Any], timezone: str) -> ScrapedEvent | None:
    """One in-person event in Israel; None when cancelled, online, abroad or with no venue."""
    venue = node.get("venue") or {}
    if node.get("status") == "CANCELLED" or node.get("eventType") != "PHYSICAL":
        return None
    if (clean_str(venue.get("country")) or "").upper() != "IL":
        return None
    title, start = clean_str(node.get("title")), parse_iso(node.get("dateTime"), timezone)
    if not title or not start or not node.get("id") or not node.get("eventUrl"):
        raise ParseError(f"meetup {node.get('id')}: event without id, url, title or start")
    group = node.get("group") or {}
    category = group.get("topicCategory") or {}
    fee = node.get("feeSettings") or {}
    price = to_decimal(fee.get("amount"))
    currency = (clean_str(fee.get("currency")) or "ILS").upper()
    address, name = clean_str(venue.get("address")), clean_str(venue.get("name"))
    photo = (node.get("featuredEventPhoto") or {}).get("highResUrl")
    return ScrapedEvent(
        source=MeetupScraper.name,
        source_event_id=str(node["id"]),
        url=node["eventUrl"],
        title=title,
        description=clean_str(node.get("description")),
        start_at=start,
        end_at=parse_iso(node.get("endTime"), timezone),
        venue_name=None if name and address and address.startswith(name) else name,  # a name that is the street
        address=address,
        city=clean_str(venue.get("city")),
        latitude=to_float(venue.get("lat")),
        longitude=to_float(venue.get("lon")),
        source_genres=["hi-tech"] if category.get("urlkey") == "technology" else [],
        event_types=[n for n in (clean_str(category.get("name")),) if n],
        organizer=clean_str(group.get("name")),
        images=[photo] if photo else [],
        currency=currency,
        min_price=price if price is not None else Decimal(0),
        max_price=price if price is not None else Decimal(0),
        # No fee settings: free to attend (RSVP).
        tickets=[
            TicketTier(
                name="RSVP" if price is None else "Fee",
                price=price or Decimal(0),
                currency=currency,
                is_free=price is None,
            )
        ],
    )
