"""Club websites built on WordPress + The Events Calendar ("tribe"), e.g. Gagarin (gagarin.co.il).

The plugin serves every upcoming event as JSON at ``/wp-json/tribe/events/v1/events``
(paged by ``next_rest_url``): title, local start / end, artwork, categories, price range
and an HTML description. These club sites rarely fill in the venue, so name, address and
city come from the config. The description usually links to the real ticket seller
(GO-OUT, Eventbuzz...); that link is kept as ``purchase_url`` so dedup merges this listing
with the seller's own. Each club is a one-line subclass, so it shows under its own name.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_HREF = re.compile(r'href="(https?://[^"]+)"')


class TribeSettings(SourceSettings):
    site_url: str
    api_path: str = "/wp-json/tribe/events/v1/events"
    per_page: int = Field(50, ge=1, le=50)
    max_pages: int = Field(10, ge=1)
    venue_name: str
    address: str | None = None
    city: str | None = None
    # Hosts of ticket sellers: the first description link to one of them is the purchase link.
    ticket_hosts: list[str] = Field(default_factory=list)
    # Site categories that are status labels ("coming soon"), not event types.
    skip_categories: list[str] = Field(default_factory=list)


class TribeScraper(BaseScraper[TribeSettings]):
    name = "tribe"
    settings_model = TribeSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(ZoneInfo(self.timezone)).date().isoformat()
        url: str | None = f"{s.site_url}{s.api_path}?per_page={s.per_page}&start_date={today}"
        items: list[dict[str, Any]] = []
        for _ in range(s.max_pages):
            if not url:
                break
            page = await self.get_json(url, expect=dict)
            items += [e for e in page.get("events") or [] if isinstance(e, dict)]
            url = page.get("next_rest_url") or None
        self.log.info("listed %d events", len(items))
        for item in items:
            try:
                if event := parse_event(item, s, self.name, self.timezone):
                    yield event
            except (ParseError, ValueError) as exc:
                self.record_failure(str(item.get("url") or item.get("id")), exc)


def parse_event(e: dict[str, Any], s: TribeSettings, source: str, timezone: str) -> ScrapedEvent | None:
    if e.get("status", "publish") != "publish" or e.get("hide_from_listings"):
        return None
    if not e.get("id") or not e.get("url") or not e.get("start_date"):
        raise ParseError("event without id, url or start date")
    tz = ZoneInfo(e.get("timezone") or timezone)
    start = datetime.fromisoformat(e["start_date"]).replace(tzinfo=tz)
    end = datetime.fromisoformat(e["end_date"]).replace(tzinfo=tz) if e.get("end_date") else None

    description = str(e.get("description") or "")
    purchase = clean_str(str(e.get("website") or "")) or _ticket_link(description, s.ticket_hosts)

    prices = sorted(p for v in (e.get("cost_details") or {}).get("values") or [] if (p := to_decimal(str(v))) is not None)
    image = e.get("image") if isinstance(e.get("image"), dict) else {}
    return ScrapedEvent(
        source=source,
        source_event_id=str(e["id"]),
        url=e["url"],
        title=html.unescape(clean_str(str(e.get("title") or "")) or ""),
        description=html_to_text(description) or None,
        start_at=start,
        end_at=end if end and end > start else None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        event_types=[
            name for c in e.get("categories") or []
            if isinstance(c, dict) and (name := clean_str(html.unescape(str(c.get("name") or "")))) and name not in s.skip_categories
        ],
        images=[image["url"]] if image.get("url") else [],
        min_price=prices[0] if prices else None,
        max_price=prices[-1] if prices else None,
        tickets=[TicketTier(name="כרטיס", price=p, is_free=p == 0) for p in prices],
        extra={"purchase_url": purchase},
    )


def _ticket_link(description: str, hosts: list[str]) -> str | None:
    for url in map(html.unescape, _HREF.findall(description)):
        host = urlparse(url).netloc.removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts):
            return url
    return None


class GagarinScraper(TribeScraper):
    name = "gagarin"
