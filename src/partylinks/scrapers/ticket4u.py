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
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import HttpUrl

from partylinks.models import ScrapedEvent
from partylinks.scrapers.eventim import EventimScraper
from partylinks.scrapers.eventim_listing import event_id_of


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
            yield event
