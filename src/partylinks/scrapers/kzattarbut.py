"""Kzat Tarbut (kzat-tarbut.com, "קצת תרבות") - a culture magazine of ~1,250 articles on shows, concerts,
stand-up, dance and kids' shows, each linking the shows it writes about to its Kupat Bravo white label
(5222.kartisim.co.il/announce/<id or slug>).

WordPress. The wp-json posts (``posts_path``, ``per_page`` at a time: the server is slow, ~1 s per post with
its content) give each article's categories (the site's genres: "הצגות", "מוסיקה", "משפחה וילדים"...; named by
one more call, ``categories_path``; ``skip_categories`` are sections, not genres) and the show links in its
text. Most articles are years old but many shows they link still play (theatre repertoire).

The white label's robots.txt disallows everything, so each show is read from the same id / slug on
kupatbravo.co.il (robots.txt allows ``/announce/``): its microdata gives every performance's start, hall,
street, city, coordinates, prices and tickets left (``kupatbravo.parse_show``). A show linked from several
articles is read once, with every article's categories. Shows are read in the site's order (newest article
first: the listing has no dates). A show with no performance left yields nothing. Events link to the
white-label page and the seance's buy page (purchase_url), which dedup keys to the same seance as `kupatbravo`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import quote, unquote

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.kupatbravo import parse_show


class KzatTarbutSettings(SourceSettings):
    site_url: str = "https://www.kzat-tarbut.com"
    posts_path: str = "/wp-json/wp/v2/posts"
    post_fields: str = "id,categories,content"
    categories_path: str = "/wp-json/wp/v2/categories?per_page=100&_fields=id,name"
    per_page: int = 25
    max_pages: int = 100
    # Categories that are sections of the magazine, not genres ("מגזין", "ביקורת הצגות", the halls' index...).
    skip_categories: list[str] = Field(default_factory=list)
    # A show link in an article: the white label's announce page, by id or slug.
    show_link: str = r"https?://5222\.kartisim\.co\.il/announce/([^/\"'?#<>\s]+)"
    details_url: str = "https://kupatbravo.co.il/announce/{show}"
    event_url: str = "https://5222.kartisim.co.il/announce/{show}"
    buy_url: str = "https://5222.kartisim.co.il/announce/buy/{seance}"
    max_shows: int = Field(3000, ge=1)


@dataclass
class Show:
    key: str  # the id or slug, as the link writes it (percent-encoded)
    categories: list[str] = field(default_factory=list)


class KzatTarbutScraper(BaseScraper[KzatTarbutSettings]):
    name = "kzattarbut"
    settings_model = KzatTarbutSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cats = await self.optional("categories", self.get_json(s.site_url + s.categories_path, expect=list)) or []
        skip = {c.casefold() for c in s.skip_categories}
        names = {
            c.get("id"): name
            for c in cats
            if isinstance(c, dict)
            and (name := clean_str(unescape(str(c.get("name") or ""))))
            and name.casefold() not in skip
        }

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "orderby": "modified", "_fields": s.post_fields}
            response = await self.request("GET", s.site_url + s.posts_path, params=params)
            rows = response.json()
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [], response.headers.get(
                "X-WP-Total"
            )

        posts = await self.paginate(page, s.per_page, s.max_pages)
        shows = collect_shows(posts, names, s.show_link)
        self.log.debug("%d articles link %d shows", len(posts), len(shows))
        if len(shows) > s.max_shows:
            self.log.warning("listing truncated at max_shows=%d (%d found)", s.max_shows, len(shows))

        async def performances(show: Show) -> list[ScrapedEvent]:
            url = s.details_url.format(show=show.key)
            return relabel(parse_show(await self.get_text(url), url, self.timezone), show, s)

        async for events in self.stream_isolated(shows[: s.max_shows], performances, lambda sh: f"show {sh.key}"):
            for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event


def collect_shows(posts: list[dict[str, Any]], categories: dict[Any, str], show_link: str) -> list[Show]:
    """Every show the articles link, in article order, with the categories of every article linking it."""
    link = re.compile(show_link)
    shows: dict[str, Show] = {}
    for post in posts:
        body = str((post.get("content") or {}).get("rendered") or "")
        labels = [c for i in post.get("categories") or [] if (c := categories.get(i))]
        for found in link.finditer(body):
            key = quote(unquote(found.group(1)), safe="-_.~")  # one spelling for "%D7%A7..." and "ק..."
            if key.lower() in {"buy", ""}:
                continue
            show = shows.setdefault(key, Show(key))
            show.categories.extend(c for c in labels if c not in show.categories)
    return list(shows.values())


def relabel(events: list[ScrapedEvent], show: Show, s: KzatTarbutSettings) -> list[ScrapedEvent]:
    """Kupat Bravo's reading of the show, as Kzat Tarbut sells it: its categories, its links."""
    out = []
    for event in events:
        update = {
            "source": KzatTarbutScraper.name,
            "url": s.event_url.format(show=show.key),
            "source_genres": list(dict.fromkeys([*(c.lower() for c in show.categories), *event.source_genres])),
            "extra": {**event.extra, "purchase_url": s.buy_url.format(seance=event.source_event_id)},
        }
        out.append(ScrapedEvent.model_validate({**event.model_dump(), **update}))
    return out
