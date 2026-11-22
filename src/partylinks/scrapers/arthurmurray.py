"""Arthur Murray Israel (arthurmurray.co.il, "ארתור מארי") - ballroom dance school in Ra'anana and Haifa:
its public evening "The Salon" (a lecture with film clips, champagne and dancing) at the Ra'anana studio.

The site is Wix. Its events page (``/events``, "האירועים שלנו") is empty (Nov 2026) and the sitemap holds
no event pages; the one dated public event is The Salon (``salon_path``). That Wix page only embeds a
landing page built for each edition (an ``HtmlComponent`` iframe on a ``*.wix-site-host.com`` host), so a
run reads three things: 1) the Wix page, whose router config names each page's JSON file and whose
``og:image`` is The Salon's photo; 2) that page JSON (``page_json_url``), for the iframe's address;
3) the landing: name and theme, the evening's parts ("הרצאה וקטעי קולנוע · ... · ריקוד": its categories,
beside the school's own ``source_genres``), date, doors and start time, the ticket cards (single / couple)
and the PayMe purchase links. Three requests a run. The students' price needs the studio's code: left out
(``skip_tier_words``). The landing's own pictures are inline base64, not artwork URLs; its video poster
(the guest's invitation) is. No Salon embedded (between editions): no events. The lessons, wedding-dance
and corporate-workshop pages are classes and services: not read.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
# "5.12.2026" at the end of the page title ("The Salon | Fred & Ginger — 5.12.2026").
_TITLE_DATE = re.compile(r"\s*[—–|-]\s*\d{1,2}\.\d{1,2}\.\d{4}\s*$")
# "כרטיס יחיד · 350 ₪" (a button) or "כרטיס זוגי\n680 ₪" (a price card).
_TIER = re.compile(r"(כרטיס[^\d·₪\n]{1,30}?)\s*(?:·\s*)?(\d[\d,]*)\s*₪")
_WIX_TRANSFORM = re.compile(r"(/media/[^/]+)/v1/.*$")


class ArthurMurraySettings(SourceSettings):
    site_url: str = "https://www.arthurmurray.co.il"
    salon_path: str = "/the-salon"
    # A Wix page's JSON (its components and their data), by the file name the router config gives.
    page_json_url: str = "https://static.wixstatic.com/sites/{name}.json.z?v=3"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    title_format: str = "{name}: {theme}"
    # The school's own field: what the evening is, beside what its text says (a lecture, film clips).
    source_genres: list[str] = Field(default_factory=lambda: ["ריקודים סלוניים"])
    ticket_hosts: list[str] = Field(default_factory=lambda: ["payme.io"])
    # Tiers sold only with the studio's code.
    skip_tier_words: list[str] = Field(default_factory=lambda: ["תלמיד"])
    # A ticket whose name holds one of these admits two.
    couple_words: list[str] = Field(default_factory=lambda: ["זוג"])


class ArthurMurrayScraper(BaseScraper[ArthurMurraySettings]):
    name = "arthurmurray"
    settings_model = ArthurMurraySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page_url = s.site_url + s.salon_path
        page = await self.optional("salon page", self.get_text(page_url))  # a removed page (404): no Salon
        if page is None:
            return
        name = page_json_name(page, s.salon_path.strip("/"))
        if not name:
            raise ParseError(f"{page_url}: no page JSON named in the router config")
        layout = await self.get_json(s.page_json_url.format(name=name), expect=dict)
        images = [image] if (image := wix_original(og_image(page))) else []
        events = []
        for url in landing_urls(layout):
            landing = await self.optional(f"landing {url}", self.get_text(url))
            if landing is None:
                continue
            try:
                event = parse_landing(landing, s, self.timezone, page_url, images)
            except Exception as exc:  # noqa: BLE001 - one odd landing never drops another
                self.record_failure(f"landing {url}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        self.log.debug("%d landing(s) on sale", len(events))
        for event in sorted(events, key=lambda e: e.start_at or datetime.min):  # nearest first
            yield event


def page_json_name(page_html: str, page_uri: str) -> str | None:
    """A Wix page -> the JSON file name of the page whose address is ``page_uri`` (its router config)."""
    match = re.search(rf'"pageUriSEO":"{re.escape(page_uri)}","pageJsonFileName":"([^"]+)"', page_html)
    return match.group(1) if match else None


def landing_urls(layout: Any) -> list[str]:
    """A Wix page JSON -> the addresses its embedded pages (``HtmlComponent`` iframes) load."""

    def walk(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            url = node.get("url")
            if node.get("type") == "HtmlComponent" and isinstance(url, str) and url.startswith(("http://", "https://")):
                yield url
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    return list(dict.fromkeys(walk(layout)))


def wix_original(url: str | None) -> str | None:
    """A Wix media URL without its resize / crop transform (``/v1/fit/w_2500,...``): the uploaded picture."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    return _WIX_TRANSFORM.sub(r"\1", unquote(url))


def _text(node: LexborNode | None) -> str:
    return node.text(separator="\n") if node is not None else ""


def parse_landing(
    page_html: str, s: ArthurMurraySettings, timezone: str, page_url: str, images: list[str] | None = None
) -> ScrapedEvent | None:
    """The Salon's landing page -> its evening; None without a name or a date."""
    tree = LexborHTMLParser(page_html)
    for node in tree.css("script, style"):
        node.decompose()
    page_title = clean_str(_text(tree.css_first("title"))) or ""
    name = clean_str(_text(tree.css_first("h1")))
    sub = tree.css_first(".sub")
    # "Fred & Ginger עם רון פוגל" / "הרצאה וקטעי קולנוע · שמפניה וכיבוד קל · ריקוד באווירה נעימה"
    sub_text = (html_to_text(sub.html) if sub is not None else None) or ""
    sub_lines = [line for x in sub_text.split("\n") if (line := clean_str(x))]
    theme = sub_lines[0] if sub_lines else None
    program = [part for x in (sub_lines[1] if len(sub_lines) > 1 else "").split("·") if (part := clean_str(x))]
    title = s.title_format.format(name=name, theme=theme) if name and theme else _TITLE_DATE.sub("", page_title)
    hero = _text(tree.css_first(".date"))
    day = _DATE.search(hero) or _DATE.search(page_title)
    if not title or not day:
        return None
    text = _text(tree.body)
    start = re.search(r"תחילת האירוע\s*(\d{1,2}):(\d{2})", hero) or re.search(r"תחילת האירוע\s*(\d{1,2}):(\d{2})", text)
    doors = re.search(r"פתיחת דלתות\s*(\d{1,2}):(\d{2})", hero) or re.search(r"פתיחת דלתות\s*(\d{1,2}):(\d{2})", text)
    clock = start or doors
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (0, 0)
    start_at = datetime(int(day[3]), int(day[2]), int(day[1]), hour, minute, tzinfo=zone(timezone))

    tiers: dict[str, TicketTier] = {}
    for match in _TIER.finditer(text):
        tier = " ".join(match.group(1).split())
        if tier in tiers or any(w in tier for w in s.skip_tier_words):
            continue
        price = to_decimal(match.group(2).replace(",", ""))
        admits = 2 if any(w in tier for w in s.couple_words) else 1
        tiers[tier] = TicketTier(name=tier, price=price, admits=admits)
    prices = [t.price for t in tiers.values() if t.price is not None]
    purchase = next(
        (
            href
            for a in tree.css("a[href]")
            if (href := (a.attributes.get("href") or "").strip())
            and any((urlsplit(href).hostname or "").endswith(h) for h in s.ticket_hosts)
            and not any(w in _text(a) for w in s.skip_tier_words)
        ),
        None,
    )
    posters = [p for v in tree.css("video[poster]") if (p := wix_original(v.attributes.get("poster")))]
    meta = tree.css_first('meta[name="description"]')
    story = tree.css_first("#story")
    about = [
        clean_str(meta.attributes.get("content")) if meta is not None else None,
        html_to_text(story.html) if story is not None else None,
    ]
    return ScrapedEvent(
        source=ArthurMurrayScraper.name,
        source_event_id=f"salon-{start_at.date().isoformat()}",
        url=page_url,
        title=title,
        description="\n\n".join(p for p in about if p) or None,
        start_at=start_at,
        time_known=clock is not None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(dict.fromkeys(g.lower() for g in [*s.source_genres, *program])),
        images=list(dict.fromkeys([*posters, *(images or [])])),
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        tickets=list(tiers.values()),
        extra={"purchase_url": purchase, "doors_open": f"{int(doors[1]):02d}:{doors[2]}" if doors else None},
    )
