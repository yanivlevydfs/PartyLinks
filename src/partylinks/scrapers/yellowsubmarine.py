"""Yellow Submarine / הצוללת הצהובה (yellowsubmarine.org.il) - live-music club at HaRechavim 13, Jerusalem.

A WordPress theme with an ``event`` post type. Its archive (``/event/``) lists every upcoming show at once
(``/event/page/2/`` repeats it; past shows live on a separate "ארכיון הופעות" page that is never read):
``div.b-artist`` cards with the link, artwork thumbnail, title, a one-line subtitle, the date without a year
("28.10") and the door / show times. Each show's page adds the full date ("28.10.2026/ יום רביעי"), the
prices ("90/105/115 ₪", "40\\50 ₪", "כניסה חופשית"), the text, the full artwork and the Eventer iframe.
Its JSON-LD is not used: it writes the Israeli clock as UTC ("21:30:00.000Z" is the 21:30 show) and is not
valid JSON (``"price": 90/105/115``). One venue: its place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal

from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, og_image, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_PRICE = re.compile(r"\d+(?:\.\d+)?")
_STAGE = re.compile(r"\d{1,2}\.\d{1,2}\s+(.+)")
_SIZE = re.compile(r"-\d+x\d+(?=\.\w+$)")  # "-600x338.jpg": a thumbnail of the full image


class YellowSubmarineSettings(SourceSettings):
    site_url: str
    listing_path: str = "/event/"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class YellowSubmarineScraper(BaseScraper[YellowSubmarineSettings]):
    name = "yellowsubmarine"
    settings_model = YellowSubmarineSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path))
        self.log.info("listed %d shows", len(cards))
        for card in cards:
            try:
                event = parse_card(card, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"show {card['url']}", exc)
                continue
            if event is None:
                continue
            page = await self.optional(f"page {card['url']}", self.get_text(card["url"]))
            if page:
                add_page(event, page)
            yield event


def _stat(node: LexborNode, icon: str) -> str:
    """The value next to an icon in a card's / page's stat row ("icon-MIC" -> "21:30")."""
    for item in node.css(".b-event-info__stat-itm"):
        if item.css_first(f"i.{icon}") and (span := item.css_first("span")):
            return clean_str(span.text()) or ""
    return ""


def parse_listing(page_html: str) -> list[dict[str, str]]:
    """Event archive -> one dict per show: url, title, subtitle, date, doors, show, image."""
    cards: dict[str, dict[str, str]] = {}
    for node in LexborHTMLParser(page_html).css("div.b-artist"):
        link = node.css_first("a.abs-link")
        url = (link.attributes.get("href") or "") if link else ""
        title = node.css_first(".b-artist__info-title")
        if not url or url in cards or title is None:
            continue
        subtitle = node.css_first(".b-artist__info-subtitle")
        day = next(
            (s.text() for s in node.css(".b-artist__footer .b-artist__info p span") if _DAY.search(s.text())), ""
        )
        image = node.css_first(".b-artist__pic img")
        cards[url] = {
            "url": url,
            "title": clean_str(title.text()) or "",
            "subtitle": clean_str(subtitle.text()) or "" if subtitle else "",
            "date": clean_str(day) or "",
            "doors": _stat(node, "icon-doors2"),
            "show": _stat(node, "icon-MIC"),
            "image": _SIZE.sub("", image.attributes.get("src") or "") if image else "",
        }
    return list(cards.values())


def show_day(text: str, today: date) -> date | None:
    """ "28.10" / "28.10.2026" -> its date; without a year: this year, or next once long past ("6.1" in October)."""
    m = _DAY.search(text)
    if not m:
        return None
    if m.group(3):
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    day = date(today.year, int(m.group(2)), int(m.group(1)))
    return day.replace(year=today.year + 1) if day < today - timedelta(60) else day


def parse_card(
    card: dict[str, str], s: YellowSubmarineSettings, timezone: str, today: date | None = None
) -> ScrapedEvent | None:
    """An archive card -> event (prices, text and ticket link come from its page); None without date or title."""
    tz = zone(timezone)
    day = show_day(card["date"], today or datetime.now(tz).date())
    if day is None or not card["title"]:
        return None
    # The show time ("מופע"), else the doors.
    clock = _TIME.search(card["show"]) or _TIME.search(card["doors"])
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (0, 0)
    # Festival nights share one title; the subtitle names the day and stage ("18.11 מגדל דוד מוזיאון"),
    # often outside the club: such a show takes that place, for the geocoder to pin.
    stage = m[1] if (m := _STAGE.match(card["subtitle"])) else None
    away = stage if stage and not any(w in stage for w in ("צוללת", "הרכבים")) else None  # HaRechavim: next door
    city = "תל אביב-יפו" if away and "תל אביב" in away else s.city
    place = away.replace("תל אביב", "").strip() if away else None
    return ScrapedEvent(
        source=YellowSubmarineScraper.name,
        source_event_id=card["url"].rstrip("/").rsplit("/", 1)[-1],
        url=card["url"],
        title=f"{card['title']} – {stage}" if stage else card["title"],
        description=card["subtitle"] or None,
        start_at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz),
        venue_name=place or s.venue_name,
        address=place or s.address,
        city=city,
        latitude=None if away else s.latitude,
        longitude=None if away else s.longitude,
        source_genres=["הופעה", "פסטיבל"] if "פסטיבל" in card["title"] else ["הופעה"],
        images=[card["image"]] if card["image"].startswith("http") else [],
        extra={"doors_open": card["doors"] or None},
    )


def add_page(event: ScrapedEvent, page_html: str) -> None:
    """The show's page: the dated day (with its year), prices, text, full artwork and the Eventer link."""
    tree = LexborHTMLParser(page_html)
    today = datetime.now(event.start_at and event.start_at.tzinfo).date()  # Israel's today, not the server's
    if (info := tree.css_first(".b-event-info")) and (day := show_day(_stat(info, "icon-calendar"), today)):
        event.start_at = event.start_at.replace(year=day.year, month=day.month, day=day.day) if event.start_at else None
    if price := tree.css_first(".b-event-info__price"):
        text = clean_str(price.text()) or ""
        prices = [Decimal(0)] if "חופשית" in text else sorted(Decimal(p) for p in _PRICE.findall(text))
        if prices:
            event.min_price, event.max_price = prices[0], prices[-1]
            event.tickets = [TicketTier(name="כרטיס", price=p, is_free=p == 0) for p in prices]
    if (body := tree.css_first(".b-event-info__body")) and (text := html_to_text(body.html)):
        event.description = text
    hero = tree.css_first(".s-hero-artist .bg-img[data-image-src]")
    image = (hero.attributes.get("data-image-src") if hero else None) or og_image(page_html)
    if image and image.startswith("http"):
        event.images = [image]
    ticket = next(
        (src for f in tree.css("iframe[src]") if "eventer.co.il" in (src := f.attributes.get("src") or "")), None
    )
    if ticket:
        event.extra = {**(event.extra or {}), "purchase_url": ticket.split("?")[0]}
