"""Meetup (meetup.com) - community meetups: tech talks, developer groups, startup and social events.

robots.txt only blocks feeds. Listing and details in one GraphQL query (``POST /gql2``, no login):
``recommendedEvents`` near a point, in-person only, cursor-paged; a point in Tel Aviv with a
150 km radius covers Israel (~40 events) and reaches Jordan and Lebanon, so only venues in Israel
are kept, and events with no venue (place sent to members) are dropped. Each event gives title,
start/end, markdown description, venue (name, address, city, coordinates), group, its group's
topic category ("technology" marks a hi-tech event) and the fee, if any.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float
from partylinks.scrapers.base import BaseScraper, SourceSettings

QUERY = """query($lat: Float!, $lon: Float!, $radius: Float, $first: Int, $after: String) {
  recommendedEvents(filter: {lat: $lat, lon: $lon, eventType: PHYSICAL, radius: $radius},
                    first: $first, after: $after, sort: {sortField: DATETIME}) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id title dateTime endTime eventUrl eventType status description
      venue { name address city lat lon country }
      group { name topicCategory { urlkey name } }
      featuredEventPhoto { highResUrl }
      feeSettings { amount currency }
    } }
  }
}"""


class MeetupSettings(SourceSettings):
    graphql_url: str
    latitude: float
    longitude: float
    radius_km: float = Field(150, gt=0)
    page_size: int = Field(50, ge=1, le=50)
    max_pages: int = Field(10, ge=1)


class MeetupScraper(BaseScraper[MeetupSettings]):
    name = "meetup"
    settings_model = MeetupSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s, cursor, seen = self.settings, None, set()
        for _ in range(s.max_pages):
            variables = {"lat": s.latitude, "lon": s.longitude, "radius": s.radius_km, "first": s.page_size,
                         "after": cursor}  # fmt: skip
            body = await self.post_json(s.graphql_url, expect=dict, json={"query": QUERY, "variables": variables})
            if body.get("errors"):
                raise ParseError(f"meetup graphql: {str(body['errors'])[:300]}")
            page = ((body.get("data") or {}).get("recommendedEvents")) or {}
            for edge in page.get("edges") or []:
                node = edge.get("node") or {}
                if node.get("id") in seen:
                    continue
                seen.add(node.get("id"))
                if event := parse_event(node, self.timezone):
                    yield event
            info = page.get("pageInfo") or {}
            cursor = info.get("endCursor")
            if not info.get("hasNextPage") or not cursor:
                return
        self.log.warning("listing truncated at max_pages=%d", s.max_pages)


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
        ],  # fmt: skip
    )
