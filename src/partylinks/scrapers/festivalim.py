"""Festivalim (festivalim.co.il) - an index of festivals, carnivals and street celebrations.

WordPress (Herald theme + the site's own "carni" plugin); robots.txt allows all but /wp-admin/.
The REST API has the posts but neither the festival dates nor its place (theme-rendered meta), so
the site's advanced search is read instead: ``/?s=&search_country=116`` (Israel) with
``search_fromdate`` = today lists festivals that have not ended yet, ten per page
(``/page/N/?...``), soonest first: title, link, categories, city, the date line ("יום רביעי, 11
בנובמבר, 2026 - יום שבת, 14  בנובמבר, 2026", days only) and an excerpt. Undated entries
("לא נמצא מידע", "שנתי באוגוסט") follow the dated ones, so paging stops at the first page
without a date. Each festival page adds the full description, the full-size image and the
festival's own site ("מידע נוסף הקשור לאירוע" > "קישורים:"), kept as ``extra["purchase_url"]``.

A festival runs over days: it starts at local midnight of its first day and, when it lasts more
than one, ends at the midnight closing its last day. Posts are reused year after year (same post,
new title and dates), so ``source_event_id`` is the post id plus the year.
Abroad festivals (most of the site, mostly free street events) are not collected.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from urllib.parse import unquote

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

ISRAEL = 116  # the search form's country id for ישראל
_POST_ID = re.compile(r"\bpost-(\d+)\b")
_DAY = re.compile(r"(\d{1,2})\s+ב([א-ת]+),?\s+(\d{4})")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], 1
    )
} | {"מרס": 3}
_THUMB_SIZE = re.compile(r"-\d+x\d+(?=\.\w+$)")


class FestivalimSettings(SourceSettings):
    site_url: str
    max_pages: int = Field(30, ge=1)


class FestivalimScraper(BaseScraper[FestivalimSettings]):
    name = "festivalim"
    settings_model = FestivalimSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        params = {
            "s": "",
            "search_category": -1,
            "search_fromdate": f"{today:%d/%m/%Y}",
            "search_todate": "",
            "search_country": ISRAEL,
            "search_state": -1,
            "search_city": -1,
        }
        events: list[ScrapedEvent] = []
        for index in range(s.max_pages):
            path = f"/page/{index + 1}/" if index else "/"
            listed = parse_listing(await self.get_text(s.site_url + path, params=params), self.timezone)
            events += listed
            if not listed:  # undated entries only from here on
                break
        else:
            self.log.warning("listing truncated at max_pages=%d", s.max_pages)
        self.log.info("listed %d dated festivals", len(events))

        async def detail(event: ScrapedEvent) -> ScrapedEvent:
            page = await self.optional(str(event.url), self.get_text(str(event.url)))
            return with_details(event, page) if page else event

        async for event in self.stream_isolated(events, detail, label=lambda e: unquote(str(e.url))):
            yield event


def parse_when(text: str | None, timezone: str) -> tuple[datetime | None, datetime | None]:
    """First day's local midnight, and the midnight after the last day when it spans several."""
    days = [date(int(y), _MONTHS[m], int(d)) for d, m, y in _DAY.findall(text or "") if m in _MONTHS]
    if not days:
        return None, None
    tz = zone(timezone)
    start = datetime(days[0].year, days[0].month, days[0].day, tzinfo=tz)
    last = days[-1] + timedelta(days=1)
    return start, datetime(last.year, last.month, last.day, tzinfo=tz) if days[-1] > days[0] else None


def _terms(node: LexborNode, kind: str) -> list[str]:
    """Names of the post's taxonomy links of one kind (category / city / country)."""
    return [
        name
        for a in node.css(".meta-category a[href]")
        if f"/{kind}/" in (a.attributes.get("href") or "") and (name := clean_str(a.text()))
    ]


def parse_listing(page_html: str, timezone: str) -> list[ScrapedEvent]:
    """The dated festivals of one search page (undated entries are skipped)."""
    events = []
    for article in LexborHTMLParser(page_html).css("article"):
        link = article.css_first(".entry-title a[href]")
        when = article.css_first(".herald-date .updated")
        post = _POST_ID.search(article.attributes.get("class") or "")
        start, end = parse_when(when.text() if when else None, timezone)
        if not (link and post and start):
            continue
        cities, categories = _terms(article, "city"), _terms(article, "category")
        image = article.css_first("img.wp-post-image")
        src = image.attributes.get("src") if image else None
        excerpt = article.css_first(".entry-content")
        events.append(
            ScrapedEvent(
                source=FestivalimScraper.name,
                source_event_id=f"{post.group(1)}-{start.year}",
                url=link.attributes["href"] or "",
                title=clean_str(link.text()) or "",
                description=html_to_text(excerpt.html) if excerpt else None,
                start_at=start,
                end_at=end,
                city=cities[0] if cities else None,
                source_genres=categories,
                event_types=categories,
                image_url=_THUMB_SIZE.sub("", src) if src else None,  # the listing shows a 300x200 crop
            )
        )
    return events


def with_details(event: ScrapedEvent, page_html: str) -> ScrapedEvent:
    """The listed festival with its page's full description, image and the festival's own site."""
    tree = LexborHTMLParser(page_html)
    content = tree.css_first(".entry-content")
    if content is not None:
        for junk in content.css(".herald-da, script, ins"):  # ad slots
            junk.decompose()
    image = tree.css_first("img.wp-post-image")
    site = next(
        (
            href
            for row in tree.css(".widget_carni_eventinfo .carni-flex-row")
            if (label := row.css_first(".h6")) and "קישורים" in label.text()
            for a in row.css("a[href]")
            if (href := a.attributes.get("href") or "").startswith("http")
        ),
        None,
    )
    # Validated again so ``images`` is rebuilt around the full-size image.
    return ScrapedEvent.model_validate(
        event.model_dump()
        | {
            "description": (html_to_text(content.html) if content else None) or event.description,
            "image_url": (image.attributes.get("src") if image else None) or event.image_url,
            "images": [],
            "extra": {**event.extra, "purchase_url": site},
        }
    )
