"""Ticket4U / Shefa (ticket4u.co.il, "שפע הפקות וכרטיסים") - the box office of Shefa Productions.

The site is an Eventim Israel tenant (``T4U``): the same Akamai, page templates and event ids as
eventim.co.il, so it is read with the Eventim reader (``EventimScraper``), configured for this site:

* Listing: the events sitemap ``/staticsite/sitemap/T4U/events1_he.xml.gz`` (gzip, ~530 events, Nov 2026;
  robots.txt names ``sitemapindex.xml``, which answers 404). Most of them are Zappa club shows, read by
  ``zappa``: the same ``skip_url_words`` as ``eventim`` leave them out.
* Order: the city pages' schema.org ItemList (``city_paths``, as ``barak``; Akamai never challenges
  them) dates the events, so the event pages are read soonest first.
* Details: every event page has Eventim's ``dataLayer`` pageMeta, ticket rows and og:image (the
  Eventim parser). Its pages answer a plain request from abroad, unlike eventim.co.il's, which
  Akamai now challenges even from our Israeli VPN (Nov 2026); our VPN gets the challenge here too.

Akamai challenges the event pages from any IP after a few (from our VPN at once, Nov 2026); each refused
page comes from Eventim's public search API (``api_url``, eventim.co.il's web id: the T4U one answers 400),
which lists every T4U event by the same id. The API's product links to eventim.co.il: the event keeps its
own ticket4u.co.il page (the sitemap's, by id), so the ticket button sells through Shefa.

Every event here is also on eventim.co.il (same id): dedup merges the two listings.

The API's ``location.city`` is often a region ("חיפה והצפון"): stored as Haifa, it filed Kiryat Motzkin's,
Yagur's and Kfar Blum's halls under Haifa (25 shows, Nov 2026). A region city becomes the town nearest the
venue's pin (``town_at``), or none without a pin; the venue name can still name it.
"""

from __future__ import annotations

import math
import re
from collections.abc import AsyncIterator
from functools import cache
from typing import Any

from pydantic import HttpUrl

from partylinks.config import CitySettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers.eventim import EventimScraper
from partylinks.scrapers.eventim_listing import event_id_of

# "חיפה והצפון", "תל אביב והמרכז", "באר שבע והדרום", "ירושלים והסביבה": a region, not a town.
_REGION = re.compile(r"\sו\s*ה?(צפון|דרום|מרכז|סביבה|סביבתה|שרון|שפלה|גליל|עמקים|קריות)$")


class Ticket4uScraper(EventimScraper):
    name = "ticket4u"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pages: dict[str, str] = {}  # event id -> its ticket4u.co.il page (the sitemap's)

    async def _list_urls(self) -> list[str]:
        urls = await super()._list_urls()
        self._pages = {event_id: url for url in urls if (event_id := event_id_of(url))}
        return urls

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        async for event in super().scrape():
            page = self._pages.get(event.source_event_id)
            if page and not str(event.url).startswith(self.settings.site_url):  # built from the API's product
                event = event.model_copy(update={"url": HttpUrl(page)})
            if event.city and _REGION.search(event.city):
                event = event.model_copy(update={"city": town_at(event.latitude, event.longitude)})
            yield event


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


@cache
def _towns() -> tuple[CitySettings, ...]:
    """The configured cities and every Israeli locality GeoNames has (kibbutzim too: Yagur, Kfar Blum)."""
    from partylinks.config import get_settings  # local: the module stays importable without settings
    from partylinks.places import israeli_places

    return tuple(c for c in (*get_settings().enrichment.cities, *israeli_places(0)) if c.center)


def town_at(lat: float | None, lon: float | None, towns: tuple[CitySettings, ...] | None = None) -> str | None:
    """The town whose centre is nearest the point, among those it lies within; None without a point or a town."""
    if lat is None or lon is None:
        return None
    near = [
        (d, c.he)
        for c in (towns if towns is not None else _towns())
        if c.center
        if (d := _distance_m((lat, lon), c.center)) <= c.radius_m
    ]
    return min(near)[1] if near else None
