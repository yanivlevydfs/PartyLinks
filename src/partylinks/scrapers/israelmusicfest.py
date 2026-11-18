"""Israel Music Festival (israelmusicfest.co.il) - חג המוזיקה הישראלית, the Culture Ministry's yearly festival of
original Israeli music: a few dozen free concerts (registration ahead) over a week, in several cities.

WordPress + Elementor. Read in three steps, about 2 requests per concert:

* ``/wp-json/wp/v2/posts`` (one call, ``_embed``): every concert post: id, page link, title ("<name> | 27.11 |
  תל אביב"), the artwork (featured image) and its city category ("תל אביב | Tel Aviv", "2.12 | חיפה").
* The concert's own page: the description (Elementor's text widget; the API's ``content`` is empty), the date and
  time headings, a "27.11, 11:00 – <hall>, <building>" line and the registration link (a Wix events page).
* The registration page's schema.org Event (JSON-LD): start / end with the zone, the venue with a street address,
  and its offer: price 0 (free with registration) and ``SoldOut`` once the places are taken. The festival page
  shows its "Sold out" button on every concert, so it says nothing about that.

Concerts are fetched soonest first (the date in the title). Posts of past years stay on the site: their dates are
over, so they are dropped. robots.txt allows everything.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from html import unescape
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import parse_page

_DAY_MONTH = re.compile(r"^(\d{1,2})\.(\d{1,2})$")
_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
# "27.11, 11:00 – אולם רקנאטי, מוזיאון תל אביב" (the dash is sometimes missing) or "אולם 207, המשכן לאמנויות, 14:00".
_WHERE = (re.compile(r"^\d{1,2}\.\d{1,2},\s*\d{1,2}:\d{2}\s*[–-]?\s*(.+)$"), re.compile(r"^(.+?),\s*\d{1,2}:\d{2}$"))
_HEBREW = re.compile(r"[א-ת]")
_FREE = "ללא עלות"


class IsraelMusicFestSettings(SourceSettings):
    site_url: str = "https://israelmusicfest.co.il"
    posts_path: str = "/wp-json/wp/v2/posts?per_page=100&_embed=wp:featuredmedia,wp:term"
    seller_host: str = "divasandgentlemen.com"  # the producer's Wix site, where places are booked
    source_genres: list[str] = Field(default_factory=lambda: ["מוזיקה ישראלית", "קונצרט", "פסטיבל"])
    # The site's city names -> ours ("מעלות" is the category of Ma'alot-Tarshiha's concerts).
    city_names: dict[str, str] = Field(default_factory=dict)
    # The festival's hall names -> the names other sources give the same hall (one venue page each).
    venue_names: dict[str, str] = Field(default_factory=dict)
    max_hours: float = 6  # a longer end is the seller's filler (20:00 "ending" 21:30 the next day)


class IsraelMusicFestScraper(BaseScraper[IsraelMusicFestSettings]):
    name = "israelmusicfest"
    settings_model = IsraelMusicFestSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        posts = await self.get_json(s.site_url + s.posts_path, expect=list)
        today = datetime.now(zone(self.timezone)).date()
        listed = sorted((p for p in map(parse_post, posts) if p), key=lambda p: p["day"] or date.max)
        coming = [p for p in listed if p["day"] is None or p["day"] >= today - timedelta(days=1)]

        async def concert(post: dict[str, Any]) -> ScrapedEvent | None:
            page = await self.get_text(post["link"])
            seller = seller_link(page, s.seller_host)
            wix = await self.optional(f"registration page {seller}", self.get_text(seller)) if seller else None
            return build_event(post, page, seller, wix, s, self.timezone)

        async for event in self.stream_isolated(coming, concert, lambda p: f"post {p['id']}"):
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def parse_post(post: dict[str, Any]) -> dict[str, Any] | None:
    """A WP post -> {id, link, title, day (the title's "27.11"), city, image}."""
    if not isinstance(post, dict) or not post.get("id") or not post.get("link"):
        return None
    raw = unescape(str((post.get("title") or {}).get("rendered") or ""))
    parts = [p.strip() for p in raw.split("|")]
    cut = next((i for i, p in enumerate(parts) if _DAY_MONTH.match(p)), len(parts))
    title = clean_str(" | ".join(parts[:cut]))
    if not title:
        return None
    embedded = post.get("_embedded") or {}
    terms = [t for group in embedded.get("wp:term") or [] for t in group if isinstance(t, dict)]
    media = next(iter(embedded.get("wp:featuredmedia") or []), {})
    return {
        "id": str(post["id"]),
        "link": str(post["link"]),
        "title": title,
        "day": _title_day(
            parts[cut] if cut < len(parts) else None, str(post.get("modified") or post.get("date") or "")
        ),
        "city": next(
            (c for t in terms if t.get("taxonomy") == "category" and (c := category_city(t.get("name")))), None
        ),
        "image": clean_str(media.get("source_url")) if isinstance(media, dict) else None,
    }


def _title_day(text: str | None, edited: str) -> date | None:
    """ "27.11" in a post last edited on 11.11.2026 -> 27.11.2026: the first such day after the edit (a month's
    slack). Posts are reused from year to year (2025's opening concert post holds 2026's), so the edit, not the
    publishing day; a post left from a past year gets a past day and is not fetched."""
    m = _DAY_MONTH.match(text or "")
    try:
        edit = date.fromisoformat(edited[:10]) - timedelta(days=31)
        day = date(edit.year, int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None
    return day if day is None or day >= edit else day.replace(year=day.year + 1)


def category_city(name: Any) -> str | None:
    """ "תל אביב | Tel Aviv" -> "תל אביב"; "2.12 | חיפה" -> "חיפה" (the Hebrew part that is not a date)."""
    parts = [p.strip() for p in unescape(str(name or "")).split("|")]
    return next((p for p in parts if _HEBREW.search(p) and not re.search(r"\d", p)), None)


def seller_link(page_html: str, host: str) -> str | None:
    tree = LexborHTMLParser(page_html)
    return next((h for a in tree.css("a[href]") if host in (h := a.attributes.get("href") or "")), None)


def _lines(page_html: str) -> list[str]:
    tree = LexborHTMLParser(page_html)
    main = tree.css_first("main") or tree.body
    return [t for line in (main.text(separator="\n") if main else "").split("\n") if (t := line.strip())]


def page_start(page_html: str, timezone: str) -> datetime | None:
    """The page's own date and time headings ("01/12/2026", "20:00")."""
    headings = [clean_str(n.text()) or "" for n in LexborHTMLParser(page_html).css(".elementor-heading-title")]
    day = next((m for h in headings if (m := _DATE.match(h))), None)
    clock = next((m for h in headings if (m := _CLOCK.match(h))), None)
    if day is None:
        return None
    at = time(int(clock[1]), int(clock[2])) if clock else time()
    return datetime.combine(date(int(day[3]), int(day[2]), int(day[1])), at, tzinfo=zone(timezone))


def page_venue(page_html: str) -> str | None:
    """The hall from the "27.11, 11:00 – <hall>" line, or the "<hall>, 14:00" one."""
    for line in _lines(page_html):
        for pattern in _WHERE:
            if (m := pattern.match(line)) and _HEBREW.search(m[1]):
                return clean_str(m[1])
    return None


def page_text(page_html: str) -> str | None:
    """The concert's text: the longest of Elementor's text widgets."""
    widgets = LexborHTMLParser(page_html).css(".elementor-widget-text-editor")
    return html_to_text(max(widgets, key=lambda n: len(n.text())).html) if widgets else None


def _street(address: str | None) -> str | None:
    """A street address; "Haifa, Israel" names only the town (a pin in its centre), so the venue is looked up instead."""
    return address if address and re.search(r"\d", address) else None


def build_event(
    post: dict[str, Any],
    page_html: str,
    seller: str | None,
    wix_html: str | None,
    s: IsraelMusicFestSettings,
    timezone: str,
) -> ScrapedEvent | None:
    """The festival post + its page + the registration page's JSON-LD -> one concert; None without a date."""
    booked = parse_page(wix_html, seller, timezone) if wix_html and seller else None
    start = booked.start_at if booked and booked.start_at else page_start(page_html, timezone)
    if start is None:
        return None
    end = booked.end_at if booked else None
    if end and not timedelta(0) < end - start <= timedelta(hours=s.max_hours):
        end = None
    venue = page_venue(page_html) or (booked.venue_name if booked else None)
    tickets = booked.tickets if booked else []
    if not tickets and _FREE in page_html:
        tickets = [TicketTier(name="כניסה חופשית בהרשמה מראש", price=0, is_free=True)]
    city = post["city"]
    return ScrapedEvent(
        source=IsraelMusicFestScraper.name,
        source_event_id=post["id"],
        url=post["link"],
        title=post["title"],
        description=page_text(page_html) or (booked.description if booked else None),
        start_at=start,
        end_at=end,
        venue_name=s.venue_names.get(venue or "", venue),
        address=_street(booked.address) if booked else None,
        city=s.city_names.get(city or "", city),
        source_genres=list(s.source_genres),
        images=[i for i in (post["image"],) if i] or (booked.images if booked else []),
        tickets=tickets,
        currency="ILS",
        min_price=min((t.price for t in tickets if t.price is not None), default=None),
        max_price=max((t.price for t in tickets if t.price is not None), default=None),
        is_sold_out=bool(tickets) and all(t.is_sold_out for t in tickets),
        organizer="חג המוזיקה הישראלית",
        extra={"purchase_url": seller} if seller else {},
    )
