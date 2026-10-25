"""Ticket House (ticket-house.com; ticketim.co.il redirects here) - tickets and packages to concerts
and sports abroad.

A Wix site. Its events are the Wix Events app, which the site's own Wix Data API answers:
``GET /_api/v1/access-tokens`` hands out an anonymous visitor token per app, and with the Wix Data
app's (``wix_data_app``) token ``POST /_api/cloud-data/v2/items/query`` queries the ``Events/Events``
collection, 100 a page with a total (~1,900 events, ~190 upcoming). An item gives the title
("artist ב<city> d.m.yyyy"), UTC start and end, the venue's time zone, venue name, address ending
in the Hebrew country, coordinates, "from" and "to" prices with their currency sign, the
registration state and the image. No detail requests. The packages (Wix Stores products) repeat
these shows with flight and hotel and have no date: not read. robots.txt allows everything but
``?lightbox=``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import cache
from typing import Any

from pydantic import Field

from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, parse_iso, to_decimal, to_float, zone
from partylinks.scrapers.abroad import airports, country_code
from partylinks.scrapers.base import BaseScraper, SourceSettings

CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD", "₪": "ILS"}

# "artist ב<city> 20.5.2027": the last " ב" before the date; a city is one or two words.
_TITLE = re.compile(r"^(.*)\sב([^\s()]+(?: [^\s()]+)?)\s+\d{1,2}\.\d{1,2}\.\d{2,4}\s*$")
# Matches: "team - team", "ריאל מדריד-מונקו" (Hebrew on both sides of a dash), leagues, cups, finals.
_SPORT = re.compile(r"[֐-׿'׳’]\s*[-–]\s*[֐-׿]|ליג|גביע|מונדיאל|גמר ")
_PRICE = re.compile(r"([€£$₪])\s*([\d,.]+)")


class TicketHouseSettings(SourceSettings):
    site_url: str
    tokens_path: str = "/_api/v1/access-tokens"
    query_path: str = "/_api/cloud-data/v2/items/query"
    event_path: str  # must contain "{slug}"
    wix_data_app: str = "675bbcef-18d8-41f5-800e-131ec9e08762"  # Wix Data's app id
    page_size: int = Field(100, ge=1, le=100)  # the API's cap
    max_pages: int = Field(30, ge=1)


class TicketHouseScraper(BaseScraper[TicketHouseSettings]):
    name = "tickethouse"
    settings_model = TicketHouseSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tokens = await self.get_json(s.site_url + s.tokens_path, expect=dict)
        auth = {"Authorization": tokens["apps"][s.wix_data_app]["instance"]}
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            body = {
                "dataCollectionId": "Events/Events",
                # registrationStatus can't be filtered on: closed ones are dropped below.
                "query": {
                    "filter": {"status": "SCHEDULED", "start": {"$gt": {"$date": now}}},
                    "sort": [{"fieldName": "start", "order": "ASC"}],
                    "paging": {"limit": s.page_size, "offset": index * s.page_size},
                },
                "returnTotalCount": True,
            }
            payload = await self.post_json(s.site_url + s.query_path, json=body, headers=auth, expect=dict)
            return [i.get("data") or {} for i in payload.get("dataItems") or []], payload.get("pagingMetadata", {}).get(
                "total"
            )

        items = await self.paginate(page, s.page_size, s.max_pages)
        events = [e for e in (parse_event(i, s.site_url + s.event_path) for i in items) if e]
        # Matches are skipped: the site is parties, shows and culture.
        sports = [e for e in events if is_sport(e.title)]
        self.log.info("listed %d events, %d on sale, %d sports matches skipped", len(items), len(events), len(sports))
        for event in events:
            if not is_sport(event.title):
                yield event


def is_sport(title: str) -> bool:
    return bool(_SPORT.search(title))


@cache
def _zone_countries() -> dict[str, str]:
    """Time zone name -> country, from the airports (fallback when the address has no country)."""
    return {a["tz"]: a["country"] for a in airports().values()}


def _price(value: Any) -> tuple[str | None, Any]:
    m = _PRICE.search(value or "")
    return (CURRENCY_SIGNS[m.group(1)], to_decimal(m.group(2).replace(",", ""))) if m else (None, None)


def _image(value: str | None) -> str | None:
    """``image://v1/<media id>/<w>_<h>/<name>`` -> the media URL."""
    m = re.match(r"image://v1/([^/]+)", value or "")
    return f"https://static.wixstatic.com/media/{m.group(1)}" if m else None


def parse_event(item: dict[str, Any], event_url: str) -> ScrapedEvent | None:
    """One Wix Events item; None for events not on sale (registration closed: sold out or placeholders)."""
    title, slug = clean_str(item.get("title")), clean_str(item.get("slug"))
    start = parse_iso((item.get("start") or {}).get("$date"))
    if not (title and slug and start) or item.get("registrationStatus") != "OPEN_TICKETS":
        return None
    tz = zone(item.get("timeZoneId"))
    address = clean_str(item.get("locationAddress"))
    # "street, city, [postcode] country"
    country = country_code(re.sub(r"\d", "", (address or "").rsplit(",", 1)[-1]))
    if country == UNKNOWN_COUNTRY:
        country = _zone_countries().get(item.get("timeZoneId") or "", UNKNOWN_COUNTRY)
    named = _TITLE.match(title)
    currency, low = _price(item.get("lowestPriceFormatted"))
    _, high = _price(item.get("highestPriceFormatted"))
    image = _image(item.get("mainImage"))
    end = parse_iso((item.get("end") or {}).get("$date"))
    return ScrapedEvent(
        source=TicketHouseScraper.name,
        source_event_id=item.get("_id") or slug,
        url=event_url.format(slug=slug),
        title=title,
        description=clean_str(item.get("description")),
        start_at=start.astimezone(tz),  # the venue's local time
        end_at=end.astimezone(tz) if end else None,
        venue_name=clean_str(item.get("locationName")),
        address=address,
        city=named.group(2) if named else None,
        latitude=to_float(item.get("latitude")),
        longitude=to_float(item.get("longitude")),
        country=country,
        artists=[a] if named and (a := clean_str(named.group(1))) else [],
        image_url=image,
        images=[image] if image else [],
        currency=currency or "ILS",
        min_price=low,
        max_price=high,
    )
