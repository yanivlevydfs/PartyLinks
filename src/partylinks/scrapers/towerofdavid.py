"""Tower of David Museum (tod.org.il) - Jerusalem's citadel museum: the Night Spectacular and King David
sound-and-light shows, gallery talks, exhibition tours, Old City walking tours, holiday tours, concerts.

WordPress with the museum's own ticketing namespace (no login, no cart needed to read):
``/wp-json/tickets/get_all_packages`` lists every package on sale (~35: package id, title, post id);
``/wp-json/tickets/get_package_events?package_id=N`` its dated slots ``{"dd/mm/yyyy": {slot id:
{"title": "HH:MM", "quantity": left}}}`` (upcoming only; a package with no dates answers 400);
``/wp-json/tickets/get_package_tickets?package_id=N`` the ticket types and prices. The package's post
``/wp-json/wp/v2/events/<post id>`` gives its page, summary, categories, artwork id and whether it is held
on site. 1 + 4 requests per package, ~140 a run. Left out: plain admission and the Phasael tower lookout
(open entry by time slot), courses and teachers' training. robots.txt disallows only cart and admin paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class TowerOfDavidSettings(SourceSettings):
    site_url: str = "https://www.tod.org.il"
    packages_path: str = "/wp-json/tickets/get_all_packages"
    slots_path: str = "/wp-json/tickets/get_package_events"
    prices_path: str = "/wp-json/tickets/get_package_tickets"
    post_path: str = "/wp-json/wp/v2/events/{id}"
    media_path: str = "/wp-json/wp/v2/media/{id}"
    categories_path: str = "/wp-json/wp/v2/events-categories"
    # Admission by time slot, not events: museum entry (1), Phasael tower lookout (3).
    skip_packages: list[int] = Field(default_factory=lambda: [1, 3])
    # Packages filed under these categories are not public events (training, courses, school programmes).
    skip_categories: list[str] = Field(default_factory=lambda: ["השתלמויות", "קורסים", "חינוך", "כניסה למוזיאון"])
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class TowerOfDavidScraper(BaseScraper[TowerOfDavidSettings]):
    name = "towerofdavid"
    settings_model = TowerOfDavidSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_json(s.site_url + s.packages_path, expect=dict)
        packages = [p for p in (listing.get("packages") or {}).values() if isinstance(p, dict)]
        packages = [p for p in packages if p.get("package_id") not in s.skip_packages]
        categories = await self.optional(
            "categories", self.get_json(s.site_url + s.categories_path, params={"per_page": 100}, expect=list)
        )
        names = {c.get("id"): clean_str(c.get("name")) for c in categories or [] if isinstance(c, dict)}
        self.log.debug("listed %d packages", len(packages))

        async def read(package: dict[str, Any]) -> list[ScrapedEvent]:
            package_id = package["package_id"]
            slots = await self._unless_400(s.slots_path, package_id)
            if not slots or not slots.get("events"):
                return []  # no dates on sale (the API answers 400 or an empty list)
            post = await self.get_json(s.site_url + s.post_path.format(id=package["post_id"]), expect=dict)
            labels = [names.get(c) for c in post.get("events-categories") or []]
            labels = [x for x in labels if x]
            if any(skip in label for label in labels for skip in s.skip_categories):
                return []
            prices = await self._unless_400(s.prices_path, package_id)
            image = None
            if media_id := (post.get("acf") or {}).get("image"):
                media = await self.optional(
                    f"package {package_id} image",
                    self.get_json(s.site_url + s.media_path.format(id=media_id), expect=dict),
                )
                image = (media or {}).get("source_url")
            events = package_events(package, post, labels, slots, prices, image, s, self.timezone)
            return [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]

        async for events in self.stream_isolated(packages, read, lambda p: f"package {p.get('package_id')}"):
            for event in events:
                yield event

    async def _unless_400(self, path: str, package_id: int) -> dict[str, Any] | None:
        """A ticketing call for one package; None on the API's 400 for a package with no dates / prices."""
        try:
            return await self.get_json(self.settings.site_url + path, params={"package_id": package_id}, expect=dict)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            return None


def package_events(
    package: dict[str, Any],
    post: dict[str, Any],
    labels: list[str],
    slots: dict[str, Any],
    prices: dict[str, Any] | None,
    image: str | None,
    s: TowerOfDavidSettings,
    timezone: str,
) -> list[ScrapedEvent]:
    """One event per dated slot of a package."""
    acf = post.get("acf") or {}
    title = clean_str(html_to_text((post.get("title") or {}).get("rendered") or "")) or clean_str(package.get("title"))
    url = clean_str(post.get("link")) or s.site_url
    on_site = "on-site" in (acf.get("location") or ["on-site"])
    tiers = [
        TicketTier(name=clean_str(p.get("title")) or "כרטיס", price=price)
        for p in ((prices or {}).get("prices") or {}).values()
        if isinstance(p, dict) and (price := to_decimal(p.get("price"))) is not None
    ]
    amounts = sorted(t.price for t in tiers if t.price is not None)
    events = []
    for day, by_id in (slots.get("events") or {}).items():
        for slot_id, slot in (by_id or {}).items():
            start = _start(day, (slot or {}).get("title"), timezone)
            if start is None or not title:
                continue
            events.append(
                ScrapedEvent(
                    source=TowerOfDavidScraper.name,
                    source_event_id=str(slot_id),
                    url=url,
                    title=title,
                    description=clean_str(acf.get("summary")),
                    start_at=start,
                    # Walking tours elsewhere in Jerusalem meet at their own spot: the museum's pin would mislead.
                    venue_name=s.venue_name if on_site else None,
                    address=s.address if on_site else None,
                    city=s.city,
                    latitude=s.latitude if on_site else None,
                    longitude=s.longitude if on_site else None,
                    source_genres=labels,
                    images=[image] if image else [],
                    tickets=tiers,
                    min_price=amounts[0] if amounts else None,
                    max_price=amounts[-1] if amounts else None,
                    is_sold_out=(slot or {}).get("quantity") == 0,
                    extra={"purchase_url": url, "package_id": package.get("package_id")},
                )
            )
    return events


def _start(day: str, hour: str | None, timezone: str) -> datetime | None:
    """ "05/11/2026" + "20:00" -> local datetime."""
    try:
        d, m, y = (int(x) for x in day.split("/"))
        h, mi = (int(x) for x in (hour or "").strip().split(":")[:2])
        return datetime(y, m, d, h, mi, tzinfo=zone(timezone))
    except ValueError:
        return None
