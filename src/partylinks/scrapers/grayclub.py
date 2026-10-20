"""Gray Club (grayclub.co.il) - live-show club; scraped for every configured branch.

The site has no JSON API, so this module parses HTML with CSS selectors that all
live in the config. Listing: the branch page's "load more" button posts
``action=load_more_shows`` to ``admin-ajax.php`` with an offset and returns card
HTML; the branch term id is read from that button, then pages are walked in
parallel until a short page. Details: each event page (price, date, door/show
time, address, show type, description, artwork, purchase link).
Artists: ``artist-sitemap.xml`` lists a page per performer (name, photo, Instagram / Facebook /
YouTube). Pages not saved yet are read, up to ``max_new_artists`` a run; the enrichment then
spots those names in event titles ("אביב בכר – מופע להקה").
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_EVENT_ID = re.compile(r"/event/(\d+)/(\d+)/?")
_PRICE = re.compile(r"\d+(?:\.\d+)?")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


class GraySelectors(BaseModel):
    load_more_button: str
    card: str
    card_link: str
    card_image: str
    title: str
    description: str
    price: str
    info_items: str
    purchase_link: str
    banner_images: str


class GrayArtistSelectors(BaseModel):
    name: str = "h1"
    image: str = ".artist-left .event_banner img"
    links: str = ".artist-img-social ul li a"


class GrayLabels(BaseModel):
    """Prefixes of the event-info lines (Hebrew UI labels)."""

    show_start: str
    doors_open: str
    address: str
    show_type: str
    parking: str


class GrayBranch(BaseModel):
    path: str  # branch page whose "load more" button carries the branch term id
    city: str


class GrayclubSettings(SourceSettings):
    site_url: str
    branches: list[GrayBranch]
    ajax_path: str
    ajax_action: str
    page_size: int = Field(50, ge=1)
    max_pages: int = Field(20, ge=1)
    selectors: GraySelectors
    labels: GrayLabels
    artist_sitemap_path: str = ""  # empty = no artist profiles
    artist_selectors: GrayArtistSelectors = Field(default_factory=GrayArtistSelectors)
    max_new_artists: int = Field(40, ge=0)


class GrayclubScraper(BaseScraper[GrayclubSettings]):
    name = "grayclub"
    settings_model = GrayclubSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        cards: dict[str, dict[str, str]] = {}
        for branch in self.settings.branches:
            for card in await self.optional(f"branch {branch.city}", self._branch_cards(branch)) or []:
                cards.setdefault(card["url"], {**card, "city": branch.city})
        self.log.info("listed %d events", len(cards))
        if self.settings.artist_sitemap_path:
            self.artist_profiles = await self.optional("artists", self._list_artists()) or []
            self.log.info("read %d new artist pages", len(self.artist_profiles))
        async for event in self.stream_isolated(cards.values(), self._fetch_event, label=lambda c: c["url"]):
            yield event

    async def _branch_cards(self, branch: GrayBranch) -> list[dict[str, str]]:
        s = self.settings
        tree = LexborHTMLParser(await self.get_text(s.site_url + branch.path))
        button = tree.css_first(s.selectors.load_more_button)
        term_id = button.attributes.get("data-categorytermid") if button else None
        if not term_id:
            raise ParseError("branch term id not found on branch page")

        async def page(index: int) -> tuple[list[dict[str, str]], None]:
            payload = await self.post_json(
                s.site_url + s.ajax_path,
                data={
                    "action": s.ajax_action,
                    "posts_per_page": index * s.page_size,  # the site's offset parameter
                    "load_more_shows": s.page_size,
                    "categorytermid": term_id,
                    "discount_category": "",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                expect=dict,
            )
            return parse_cards(str(payload.get("htmldata") or ""), s.selectors), None

        return await self.paginate(page, s.page_size, s.max_pages)

    async def _fetch_event(self, card: dict[str, str]) -> ScrapedEvent:
        return parse_event(await self.get_text(card["url"]), card, self.settings, self.timezone)


    async def _list_artists(self) -> list[ArtistProfile]:
        s = self.settings
        sitemap = await self.get_text(s.site_url + s.artist_sitemap_path)
        known = {p.profile_url for p in self.known_artists.values()}
        pages = [u for u in dict.fromkeys(_LOC.findall(sitemap)) if u not in known][: s.max_new_artists]
        profiles = []
        for url in pages:
            page = await self.optional(f"artist {url}", self.get_text(url))
            if page and (profile := parse_artist_page(page, url, s.artist_selectors)):
                profiles.append(profile)
        return profiles


def parse_artist_page(page_html: str, url: str, sel: GrayArtistSelectors) -> ArtistProfile | None:
    tree = LexborHTMLParser(page_html)
    name_node = tree.css_first(sel.name)
    name = clean_str(name_node.text()) if name_node else None
    if not name:
        return None
    image = tree.css_first(sel.image)
    links = [h for a in tree.css(sel.links) if (h := a.attributes.get("href")) and h.startswith("http")]
    return ArtistProfile(
        name=name,
        source=GrayclubScraper.name,
        image_url=image.attributes.get("src") if image else None,
        profile_url=url,
        links=list(dict.fromkeys(links)),
    )


def parse_cards(fragment: str, sel: GraySelectors) -> list[dict[str, str]]:
    cards = []
    for node in LexborHTMLParser(fragment).css(sel.card):
        link = node.css_first(sel.card_link)
        href = link.attributes.get("href") if link else None
        if not href or not _EVENT_ID.search(href):
            continue
        image = node.css_first(sel.card_image)
        cards.append({"url": href, "image": (image.attributes.get("src") or "") if image else ""})
    return cards


def parse_event(page_html: str, card: dict[str, str], s: GrayclubSettings, timezone: str) -> ScrapedEvent:
    match = _EVENT_ID.search(card["url"])
    if not match:
        raise ParseError("unexpected event url")
    tree = LexborHTMLParser(page_html)
    sel, labels = s.selectors, s.labels
    info = _info_lines(tree, sel.info_items)

    day = next((m for line in info if (m := _DATE.search(line))), None)
    start = _labelled(info, labels.show_start) or _labelled(info, labels.doors_open)
    start_at = None
    if day:
        hour, minute = (_TIME.search(start).groups() if start and _TIME.search(start) else ("0", "0"))
        start_at = datetime(
            int(day.group(3)), int(day.group(2)), int(day.group(1)), int(hour), int(minute), tzinfo=ZoneInfo(timezone)
        )

    price_node = tree.css_first(sel.price)
    # First number only: "80-120 ₪" is 80, not 80120.
    price_match = _PRICE.search(price_node.text()) if price_node else None
    price = to_decimal(price_match.group(0)) if price_match else None
    purchase = tree.css_first(sel.purchase_link)
    og_image = tree.css_first('meta[property="og:image"]')
    images = [
        *(img.attributes.get("src") for img in tree.css(sel.banner_images)),
        og_image.attributes.get("content") if og_image else None,
        card.get("image"),
    ]
    title_node = tree.css_first(sel.title)
    description_node = tree.css_first(sel.description)
    show_type = _labelled(info, labels.show_type)

    tickets = (
        [TicketTier(name="כרטיס", price=price, is_free=price == 0)] if price is not None else []
    )
    return ScrapedEvent(
        source=GrayclubScraper.name,
        source_event_id=f"{match.group(1)}-{match.group(2)}",
        url=card["url"],
        title=clean_str(title_node.text()) if title_node else "",
        description=html_to_text(description_node.html) if description_node else None,
        start_at=start_at,
        venue_name=f"Gray Club {card.get('city') or ''}".strip(),
        address=_labelled(info, labels.address),
        city=card.get("city") or None,
        event_types=[show_type] if show_type else [],
        images=[i for i in images if isinstance(i, str) and i.startswith("http")],
        min_price=price,
        max_price=price,
        tickets=tickets,
        extra={
            "doors_open": _labelled(info, labels.doors_open),
            "parking": _labelled(info, labels.parking),
            "purchase_url": purchase.attributes.get("href") if purchase else None,
        },
    )


def _info_lines(tree: LexborHTMLParser, selector: str) -> list[str]:
    return [text for node in tree.css(selector) if (text := clean_str(_text(node)))]


def _text(node: LexborNode) -> str:
    return " ".join(node.text(separator=" ").split())


def _labelled(lines: list[str], label: str) -> str | None:
    for line in lines:
        if line.startswith(label):
            return clean_str(line[len(label) :].lstrip(": "))
    return None

