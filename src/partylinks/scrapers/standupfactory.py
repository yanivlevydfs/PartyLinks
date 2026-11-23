"""Stand Up Factory (standupfactory.co.il, "סטנדאפ פקטורי") - Tel Aviv's stand-up club (שביל המרץ 5),
a show every night: comedians' own sets, the house line-up nights ("שישי המצחיק", "שלישי המצחיק"), material
nights, uncensored and English stand-up.

The site is WordPress (Divi). Every date is its own post. The show board ("לוח הופעות", ``board_path``) lists
the coming dates, nearest first, 18 to a page (``?paged=N``, ~6 pages): the post id, the title, "23/11/2026 יום
ב' 21:30", the square and the cover artwork (a sold-out date's file names say "אזלו"). Each date's post page
holds only GoShow's sale iframe (``/show/<show id>/<date id>/``): that GoShow page is the purchase link, and
`goshow` reads the same date with its text and prices (dedup merges them). The posts' categories (one wp-json
call for the listed ids, with their permalinks, one for the names) are the comedian (an artist) or the night's
series ("ערב גסויות", "סטנדאפ באנגלית": genres); "כללי" (general) is none. No price or text here: both are GoShow's. Entry from 18.

Access: Cloudflare answers Railway's own IP with an empty "202"; the source proxy and the VPN get the pages.
That 202 counts as a block (``is_blocked``), so the run moves to the next proxy instead of reading an empty board.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_WHEN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\D+?(\d{1,2}):(\d{2})")
_GOSHOW = re.compile(r"goshow\.co\.il/(?:show|event)/(\d+)/(\d+)")


class StandupFactorySettings(SourceSettings):
    site_url: str = "https://www.standupfactory.co.il"
    board_path: str = "/%d7%9c%d7%95%d7%97-%d7%94%d7%95%d7%a4%d7%a2%d7%95%d7%aa/"  # /לוח-הופעות/
    max_pages: int = 20
    posts_path: str = "/wp-json/wp/v2/posts"
    categories_path: str = "/wp-json/wp/v2/categories?per_page=100&_fields=id,name"
    goshow_url: str = "https://www.goshow.co.il/show/{show_id}/{date_id}/"
    venue_name: str = "סטנדאפ פקטורי"
    address: str = "שביל המרץ 5, תל אביב-יפו"
    city: str = "תל אביב-יפו"
    latitude: float | None = None
    longitude: float | None = None
    min_age: int = 18
    # Every date is a stand-up show. Categories: a night's series (``series_categories``: a genre), none (the
    # default "כללי"), else the comedian (an artist).
    source_genres: list[str] = Field(default_factory=lambda: ["סטנדאפ"])
    series_categories: list[str] = Field(default_factory=list)
    ignore_categories: list[str] = Field(default_factory=lambda: ["כללי"])
    sold_out_mark: str = "אזלו"  # in a sold-out date's artwork file names


@dataclass
class BoardCard:
    post_id: str
    url: str
    title: str
    start_at: datetime
    images: list[str] = field(default_factory=list)
    sold_out: bool = False


class StandupFactoryScraper(BaseScraper[StandupFactorySettings]):
    name = "standupfactory"
    settings_model = StandupFactorySettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Cloudflare's answer to an IP it holds back (Railway's own: "202", a 200-byte page; a challenge)."""
        return response.status_code == 202 or response.headers.get("cf-mitigated") == "challenge"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        first = await self.get_text(s.site_url + s.board_path)
        cards = parse_board(first, s, self.timezone)
        for index in range(2, min(last_page(first), s.max_pages) + 1):  # nearest first: page by page
            cards += parse_board(await self.get_text(f"{s.site_url}{s.board_path}?paged={index}"), s, self.timezone)
        cards = list({c.post_id: c for c in cards}.values())  # a date on two pages (the board moved): once
        self.log.debug("listed %d dates", len(cards))
        posts = await self.optional("posts", self._posts([c.post_id for c in cards])) or {}

        async def detail(card: BoardCard) -> ScrapedEvent:
            link, categories = posts.get(card.post_id, (None, []))
            card.url = link or card.url
            return build_event(card, goshow_link(await self.get_text(card.url), s), categories, s)

        events = [e async for e in self.stream_isolated(cards, detail, lambda c: f"post {c.post_id}")]
        for event in sorted(events, key=lambda e: (e.start_at, e.source_event_id)):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event

    async def _posts(self, post_ids: list[str]) -> dict[str, tuple[str | None, list[str]]]:
        """Post id -> its permalink and category names (the comedian, the series), from wp-json: 1 call per 100
        posts + 1 for the names."""
        s = self.settings
        names = {
            c.get("id"): clean_str(unescape(str(c.get("name") or "")))
            for c in await self.get_json(s.site_url + s.categories_path, expect=list)
            if isinstance(c, dict)
        }
        out: dict[str, tuple[str | None, list[str]]] = {}
        for start in range(0, len(post_ids), 100):
            chunk = post_ids[start : start + 100]
            params = {"include": ",".join(chunk), "per_page": 100, "_fields": "id,link,categories"}
            for post in await self.get_json(s.site_url + s.posts_path, expect=list, params=params):
                if isinstance(post, dict):
                    link = str(post.get("link") or "")
                    out[str(post.get("id"))] = (
                        link if link.startswith(("http://", "https://")) else None,
                        [
                            n
                            for i in post.get("categories") or []
                            if (n := names.get(i)) and n not in s.ignore_categories
                        ],
                    )
        return out


def last_page(page_html: str) -> int:
    """The board's last page number (its pagination links), 1 without them."""
    tree = LexborHTMLParser(page_html)
    numbers = [int(t) for n in tree.css(".pagination .page-numbers") if (t := n.text(strip=True)).isdigit()]
    return max(numbers, default=1)


def parse_board(page_html: str, s: StandupFactorySettings, timezone: str) -> list[BoardCard]:
    """One board page -> its dates (post id, title, start, artwork)."""
    tree = LexborHTMLParser(page_html)
    tz = zone(timezone)
    cards = []
    for link in tree.css(".shows-block .shows-flex a[href]"):
        href = link.attributes.get("href") or ""
        post_id = (parse_qs(urlparse(href).query).get("p") or [""])[0]
        heading, when = link.css_first("h2"), link.css_first("h3")
        match = _WHEN.search(when.text() if when else "")
        title = clean_str(unescape(heading.text())) if heading else None
        if not post_id.isdigit() or not match or not title:
            continue
        day, month, year, hour, minute = (int(x) for x in match.groups())
        images = [
            _encoded(src)
            for img in (link.css_first("img.mobile-image"), link.css_first("img.desktop-image"))
            if img is not None and (src := img.attributes.get("src") or "").startswith(("http://", "https://"))
        ]
        cards.append(
            BoardCard(
                post_id=post_id,
                url=f"{s.site_url}/?p={post_id}",
                title=title,
                start_at=datetime(year, month, day, hour, minute, tzinfo=tz),
                images=images,
                sold_out=any(s.sold_out_mark in i or quote(s.sold_out_mark) in i for i in images),
            )
        )
    return cards


def goshow_link(page_html: str, s: StandupFactorySettings) -> str | None:
    """The date's GoShow page, from the post's sale iframe."""
    tree = LexborHTMLParser(page_html)
    for frame in tree.css("iframe[src]"):
        if match := _GOSHOW.search(frame.attributes.get("src") or ""):
            return s.goshow_url.format(show_id=match.group(1), date_id=match.group(2))
    return None


def build_event(card: BoardCard, seller: str | None, categories: list[str], s: StandupFactorySettings) -> ScrapedEvent:
    extra: dict[str, Any] = {"purchase_url": seller} if seller else {}
    series = [c for c in categories if c in s.series_categories]
    return ScrapedEvent(
        source=StandupFactoryScraper.name,
        source_event_id=card.post_id,
        url=card.url,
        title=card.title,
        start_at=card.start_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(dict.fromkeys(g.lower() for g in [*s.source_genres, *series])),
        artists=[c for c in categories if c not in series],
        images=card.images,
        min_age=s.min_age,
        is_sold_out=card.sold_out,
        organizer=s.venue_name,
        extra=extra,
    )


def _encoded(url: str) -> str:
    # Hebrew file names ("לירון-אופיר-קאבר-אחרונים.gif") percent-encoded, as a browser sends them.
    return quote(url, safe=":/%?=&")
