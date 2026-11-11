"""ShowsHappening (showshappening.com) - ticketing in Malta, the UK, Germany and Spain: clubbing,
classical concerts, live music, theatre, comedy, kids' shows, excursions and museums.

Listing: each country's home page (``/?country=mt|gb|de|es``) is a calendar of every upcoming show:
one link per date with the event's path, the date, its categories and tags, the flyer and title.
Details: the event page's own HTML (``og:description``, ``og:image``, the hidden ``showdate`` with
the next show's day and time, ``eventid``). One event per listed date; a recurring event's other
dates take the next show's time of day. Venue, address and its Google point come from the page's hidden
fields (``EventVenueName``, ``EventVenueAddress``, ``EventVenueGoogleLocation``); the newer pages, drawn
by JavaScript, carry none of them: their venue (name, address, point) comes from the site's own API,
apicore.showshappening.com ``/events/<eventid>`` with its public app key, one request per such event.
Prices: the page names its next show (the ``SelectEventShowsToBuyTicket`` option, or the newer page's hidden ``showid``) and the
site's own ``/MyEvents/GetAvailableTicketTypesWithPrices?id=<show>`` returns its ticket types (name,
"€20.00", booking fee, ``DisabledType`` 2 = sold out); one request per event. Newer pages that name no
show load their shows from apicore.showshappening.com (app key) only: no price then.
robots.txt allows everything.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, to_decimal
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD = re.compile(
    r'<a href="(/[^"]+)" class="calendar_events_result_event_container" data-show-date="([^"]*)"\s*'
    r'data-category="([^"]*)" data-tags="([^"]*)">\s*<img src="([^"]*)"[^>]*>\s*<p>(.*?)</p>', re.DOTALL)
_META = re.compile(r'<meta property="(og:[a-z]+|description)" content="([^"]*)"')
_SHOWDATE = re.compile(r'class="showdate" value="[A-Za-z]+ (\d{1,2}-[A-Za-z]{3}-\d{4}) (\d{1,2}:\d{2})"')
# The page's hidden fields: venue, its address and its Google point "(35.91,14.49)".
_HIDDEN = re.compile(r'id="(EventVenueName|EventVenueAddress|EventVenueGoogleLocation)" value="([^"]*)"')
_POINT = re.compile(r"^\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)?$")
_EVENTID = re.compile(r'id="eventid" value="([0-9a-f-]+)"')
# The next show: the older page's show picker, the newer page's hidden field.
_SHOW = re.compile(r'id="SelectEventShowsToBuyTicket"[^>]*>\s*<option[^>]*value="([0-9a-f-]{36})"|class="showid" value="([0-9a-f-]{36})"')
_SOLD_OUT = re.compile(r"All tickets are sold out", re.IGNORECASE)
_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD"}
_CITY = re.compile(r'<input type="hidden" name="city" value="([^"]+)"')

# ShowsHappening's categories and tags as the category words the enrichment knows. A tag that only
# describes the mood ("Romantic", "Night", "Indoor") adds nothing.
_CATEGORIES = {
    "clubbing and parties": "nightclubs", "classical music": "classical", "live music": "concert",
    "arts and culture": "culture", "kids": "kids", "comedy": "comedy", "educational": "lecture",
    "sports": "sports", "health and fitness": "wellness", "excursions and experiences": "tours",
}
_TAGS = {
    "theatre": "theatre", "pantomine": "theatre", "musical": "musicals", "opera": "opera", "choir": "classical",
    "orchestra": "classical", "christmas": "holidays", "halloween": "halloween", "jazz": "jazz", "rock": "rock",
    "techno": "techno", "house": "house", "afro house": "afro house", "hip hop": "hip hop", "electronic": "electronic",
    "disco": "disco", "festival": "festival", "dance show": "dance", "food & drink": "food", "stand up": "comedy",
    "comedy night": "comedy", "visual arts": "exhibitions", "museum": "exhibitions", "beach club": "nightclubs",
    "clubbing": "nightclubs", "children": "kids", "family-friendly": "family", "tribute": "concert",
}


class ShowsHappeningSettings(SourceSettings):
    site_url: str
    countries: list[str] = Field(default_factory=lambda: ["mt", "gb", "de", "es"])
    prices_path: str = "/MyEvents/GetAvailableTicketTypesWithPrices"  # the show's ticket types (?id=<show>)
    # The site's API (its own pages' app key): an event's details, the venue of pages without hidden fields.
    api_url: str = "https://apicore.showshappening.com/showshappening/1"
    api_key: str = "0d3184c9-9437-4f17-af6a-6192491bb700"


class ShowsHappeningScraper(BaseScraper[ShowsHappeningSettings]):
    name = "showshappening"
    settings_model = ShowsHappeningSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed: dict[str, dict[str, Any]] = {}
        for country in self.settings.countries:
            page = await self.get_text(self.settings.site_url + "/", params={"country": country})
            for card in cards(page, country):
                entry = listed.setdefault(card["path"], {**card, "dates": []})
                entry["dates"].append(card["date"])
        self.log.debug("listed %d events (%d shows)", len(listed), sum(len(e["dates"]) for e in listed.values()))
        async for events in self.stream_isolated(list(listed.values()), self._fetch_event, label=lambda e: e["path"]):
            for event in events:
                yield event

    async def _fetch_event(self, listed: dict[str, Any]) -> list[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + listed["path"])
        # ponytail: the next show's prices stand for every date; per-show prices need one request per date.
        types = None
        if show := show_id(page):
            types = await self.optional("ticket types", self.get_json(s.site_url + s.prices_path, expect=list, params={"id": show}))
        venue = None
        if not _HIDDEN.search(page) and (m := _EVENTID.search(page)):
            headers = {"appkey": s.api_key, "accept": "application/json"}
            details = await self.optional("venue", self.get_json(f"{s.api_url}/events/{m.group(1)}", expect=dict, headers=headers))
            venue = (details or {}).get("venue")
        return parse_event(listed, page, s.site_url, self.timezone, types, venue if isinstance(venue, dict) else None)


def cards(page_html: str, country: str) -> list[dict[str, Any]]:
    """The calendar's show links: path, date, categories, tags, flyer, title and country."""
    out = []
    for path, day, categories, tags, image, title in _CARD.findall(page_html):
        try:
            date = datetime.strptime(day, "%Y-%B-%d").date()
        except ValueError:
            continue
        out.append({
            "path": "/" + html_lib.unescape(path).lstrip("/"),  # UK links come as "//e/gb/..."
            "date": date, "country": country.upper(),
            "categories": [c.strip() for c in html_lib.unescape(categories).split(",") if c.strip()],
            "tags": [t.strip() for t in html_lib.unescape(tags).split(",") if t.strip()],
            "image": html_lib.unescape(image), "title": clean_str(html_lib.unescape(title)),
        })
    return out


def show_id(page_html: str) -> str | None:
    """The next show's id, which the price call takes; None when the page names none."""
    m = _SHOW.search(page_html)
    return (m.group(1) or m.group(2)) if m else None


def ticket_tiers(types: list[Any] | None) -> list[TicketTier]:
    """The show's ticket types: "€20.00" and its booking fee; ``DisabledType`` 2 is sold out, 3/4 not on sale."""
    tiers = []
    for row in types or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("Price") or "")
        price = to_decimal(re.sub(r"[^\d.]", "", text))
        name = clean_str(row.get("Name"))
        if price is None or not name:
            continue
        disabled = row.get("DisabledType") or 0
        fee = to_decimal(row.get("BookingFee"))
        tiers.append(TicketTier(
            source_ticket_id=clean_str(row.get("TicketTypeId")),
            name=name,
            description=clean_str(row.get("Description")),
            price=price,
            service_fee=fee or None,
            currency=next((c for sign, c in _SIGNS.items() if sign in text), "EUR"),
            status=TierStatus.SOLD_OUT if disabled == 2 else TierStatus.CLOSED if disabled else TierStatus.ON_SALE,
        ))
    return tiers


def categories(listed: dict[str, Any]) -> list[str]:
    """Category words for the enrichment from the event's categories and tags."""
    words = [_CATEGORIES.get(c.casefold()) for c in listed["categories"]] + [_TAGS.get(t.casefold()) for t in listed["tags"]]
    return list(dict.fromkeys(w for w in words if w))


def parse_event(
    listed: dict[str, Any], page_html: str, site_url: str, default_tz: str, types: list[Any] | None = None,
    venue: dict[str, Any] | None = None,
) -> list[ScrapedEvent]:
    """One event per listed date that is not over yet. ``venue``: the API's (name, address, coordinates),
    for pages without the hidden venue fields."""
    meta = {k: html_lib.unescape(v) for k, v in _META.findall(page_html)}
    # UK pages are drawn by JavaScript: their og:title is the site's ("ShowsHappening | Tickets in Malta").
    title = listed["title"] or clean_str((meta.get("og:title") or "").removesuffix("| ShowsHappening"))
    city = clean_str(html_lib.unescape(m.group(1))) if (m := _CITY.search(page_html)) else None
    event_id = (m.group(1) if (m := _EVENTID.search(page_html)) else None) or listed["path"].strip("/").replace("/", "-")
    if not title:
        raise ParseError(f"{listed['path']}: event without title")
    country = listed["country"]
    zone = ZoneInfo(local_zone("GB" if country == "UK" else country, default_tz))
    hidden = {k: clean_str(html_lib.unescape(v)) for k, v in _HIDDEN.findall(page_html)}
    for field, key in (("EventVenueName", "name"), ("EventVenueAddress", "address"), ("EventVenueGoogleLocation", "coordinates")):
        hidden[field] = hidden.get(field) or clean_str((venue or {}).get(key))
    point = _POINT.match(hidden.get("EventVenueGoogleLocation") or "")
    lat, lng = (float(point.group(1)), float(point.group(2))) if point else (None, None)
    show = _SHOWDATE.search(page_html)
    clock: time | None = datetime.strptime(show.group(2), "%H:%M").time() if show else None
    image = clean_str(meta.get("og:image")) or clean_str(listed["image"])
    tiers = ticket_tiers(types)
    sold_out = bool(_SOLD_OUT.search(page_html))
    now = datetime.now(UTC)
    events = []
    # ponytail: a recurring event's other dates take the next show's hour; per-date hours need the API.
    for day in sorted(set(listed["dates"])):
        start = datetime.combine(day, clock or time(), zone)
        if start.date() < now.astimezone(zone).date():  # never past events
            continue
        events.append(ScrapedEvent(
            source=ShowsHappeningScraper.name,
            source_event_id=f"{event_id}-{day.isoformat()}",
            url=site_url + listed["path"],
            title=title,
            description=clean_str(meta.get("og:description") or meta.get("description")),
            city=city,
            venue_name=hidden.get("EventVenueName"),
            address=hidden.get("EventVenueAddress"),
            latitude=lat if lat and lng else None,
            longitude=lng if lat and lng else None,
            start_at=start,
            time_known=clock is not None,
            country=country,
            source_genres=categories(listed),
            image_url=image,
            images=[image] if image else [],
            currency=tiers[0].currency if tiers else "GBP" if country == "GB" else "EUR",
            tickets=[t.model_copy() for t in tiers],
            is_sold_out=sold_out,
        ))
    return events
