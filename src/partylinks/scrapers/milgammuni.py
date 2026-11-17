"""Municipal sites on Metropolinet's city multisite (WordPress + Elementor, city-multisite.milgam.co.il):
bet-shean.org.il, ...

The REST API answers 401 and there is no event feed or iCal. The events page (``/אירועים/``) is one
Unlimited Elements grid of every ``events`` post (~70, newest first, no dates on the cards): link,
title and artwork. Each event page gives, in Elementor icon boxes, "תאריך ושעה" (``17/11/2026 10:00``),
"מיקום האירוע" (``בית יד לבנים | רחוב דוד רמז 6``, ``מרכז הנוער - רח' הרב לסרי 2`` or a bare place),
"מה קורה באירוע" (the description, which states the price when there is one: "כרטיסים ברכישה מוקדמת:
40 ₪") and a "לפרטים והרשמה" button (a registration / ticket link, often a did.li short link, kept as
``extra["purchase_url"]``). Posts are listed in publishing order, which follows the event dates, so
pages are read until ``stop_after_past`` past events in a row: a run reads the current events and a
few past ones, never the archive. The site's topic filter (``topic`` taxonomy) is not on the event
pages, so genres come from the title and description (source default: culture). robots.txt is empty.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.matnasnet import covered_smarticket_hosts
from partylinks.scrapers.text_prices import text_tickets

_WHEN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
# Chat groups, social profiles and sign-up forms are not a seller.
_NOT_SELLERS = ("wa.me", "whatsapp.com", "facebook.com", "instagram.com", "forms.gle", "docs.google.com")


class MilgamSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://bet-shean.org.il
    city: str
    listing_path: str = "/%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d/"  # /אירועים/
    organizer: str | None = None  # default: עיריית <city>


class MilgamMuniSettings(SourceSettings):
    sites: list[MilgamSite] = Field(min_length=1)
    # Past events in a row (in the grid's newest-first order) after which a site's pages stop being read.
    stop_after_past: int = Field(default=3, ge=1)


class MilgamMuniScraper(BaseScraper[MilgamMuniSettings]):
    name = "milgammuni"
    settings_model = MilgamMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        skip = covered_smarticket_hosts()
        for site in self.settings.sites:
            listing = await self.optional(f"{site.url} listing", self.get_text(site.url + site.listing_path))
            cards = parse_listing(listing or "", site.url)
            self.log.debug("%s: %d cards", site.city, len(cards))
            past_in_row = 0
            for url, image in cards:
                page = await self.optional(url, self.get_text(url))
                if page is None:
                    continue
                try:
                    event = parse_event(page, url, image, site, self.timezone)
                except ParseError as exc:
                    self.record_failure(url, exc)
                    continue
                if event is None:
                    continue
                if not is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                ):
                    past_in_row += 1
                    if past_in_row >= self.settings.stop_after_past:
                        break
                    continue
                past_in_row = 0
                purchase = event.extra.get("purchase_url") or ""
                if (urlsplit(purchase).hostname or "") in skip:  # sold by a box office smarticket reads
                    continue
                yield event


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def parse_listing(page_html: str, base: str) -> list[tuple[str, str | None]]:
    """(event page URL, artwork URL) of every grid card, each once, in the grid's order."""
    cards: dict[str, str | None] = {}
    for item in LexborHTMLParser(page_html).css("div.ue_post_grid_item"):
        link = item.css_first("a.ue_post_full_link[href]") or item.css_first(".uc_post_title a[href]")
        href = clean_str(link.attributes.get("href")) if link else None
        if not href:
            continue
        img = item.css_first(".uc_post_image img")
        src = clean_str(img.attributes.get("src")) if img else None
        cards.setdefault(urljoin(base + "/", href), src if src and src.startswith("http") else None)
    return list(cards.items())


def _boxes(tree: LexborHTMLParser) -> dict[str, LexborNode]:
    """Elementor icon boxes by heading: "תאריך ושעה", "מיקום האירוע", "מה קורה באירוע"."""
    boxes: dict[str, LexborNode] = {}
    for heading in tree.css("h3.elementor-icon-box-title"):
        content = heading.parent
        desc = content.css_first("p.elementor-icon-box-description") if content else None
        if desc is not None:
            boxes.setdefault(_text(heading), desc)
    return boxes


def split_place(place: str | None) -> tuple[str | None, str | None]:
    """Venue and address of "בית יד לבנים | רחוב דוד רמז 6" / "מרכז הנוער - רח' הרב לסרי 2"."""
    if not place:
        return None, None
    venue, *address = re.split(r"\s+\|\s+" if "|" in place else r"\s+[-–]\s+", place, maxsplit=1)
    return clean_str(venue), clean_str(address[0]) if address else None


def _seller_link(tree: LexborHTMLParser, base: str) -> str | None:
    """The page's "לפרטים והרשמה" button: the first gradient button leaving the site (the header's
    "פנייה למוקד" is the site's own), unless it is a chat group, a social profile or a form."""
    own = urlsplit(base).hostname
    for button in tree.css("div[id^=uc_gradient_hover_effect_button] a[href]"):
        link = clean_str(button.attributes.get("href")) or ""
        host = urlsplit(link).hostname or ""
        if link.startswith("http") and host != own:
            return None if any(host == h or host.endswith("." + h) for h in _NOT_SELLERS) else link
    return None


def parse_event(page_html: str, url: str, image: str | None, site: MilgamSite, timezone: str) -> ScrapedEvent | None:
    """The event of one page; None when it states no date."""
    tree = LexborHTMLParser(page_html)
    title = _text(tree.css_first("h1"))
    if not title:
        raise ParseError(f"{url}: no title")
    boxes = _boxes(tree)
    found = _WHEN.search(_text(boxes.get("תאריך ושעה")))
    if not found:
        return None
    d, m, y, hh, mm = found.groups()
    start = datetime(int(y), int(m), int(d), int(hh or 0) % 24, int(mm or 0), tzinfo=zone(timezone))
    venue, address = split_place(clean_str(_text(boxes.get("מיקום האירוע"))))
    about = boxes.get("מה קורה באירוע")
    description = html_to_text(about.html) if about is not None and about.html else None
    purchase = _seller_link(tree, site.url)
    art = tree.css_first(".elementor-widget-theme-post-featured-image a[href]")
    artwork = clean_str(art.attributes.get("href")) if art else None
    post = re.search(r"\bpostid-(\d+)\b", (tree.body.attributes.get("class") or "") if tree.body else "")
    tickets = text_tickets(description)
    prices = [t.price for t in tickets if t.price is not None]
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return ScrapedEvent(
        source=MilgamMuniScraper.name,
        source_event_id=f"{urlsplit(site.url).hostname}:{post.group(1) if post else slug}",
        url=url,
        title=title,
        description=description,
        start_at=start,
        time_known=bool(hh),
        venue_name=venue,
        address=address,
        city=site.city,
        organizer=site.organizer or f"עיריית {site.city}",
        image_url=artwork if artwork and artwork.startswith("http") else image,
        tickets=tickets,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        extra={"purchase_url": purchase} if purchase else {},
    )
