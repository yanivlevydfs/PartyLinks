"""Matnasnet (מתנסנט, also ``*.atarix.co.il``) - the CMS most community centres and some local
councils run (givatzeev.org.il, mymatnas.org.il, hof-hasharon.atarix.co.il, ...).

Every site has ``page.php?type=events`` and one page per event ``page.php?type=event&id=N``, but
each site picks one of a dozen themes, so the listing is read only for its event links (the ones
above "ארכיון אירועים", the archive; a calendar theme lists one month, so ``months`` more month
pages are read). The event page is read the same way on every theme: its schema.org ``Event``
JSON-LD gives the title, days, image, price and description; the time comes from the "add to
calendar" link (``dates=20261027T173000/...``) or the page's "שעה" / "בשעות" line, the place from
the calendar file's ``LOCATION:`` or the theme's place field, categories from the theme's tags
(only some themes show them; enrichment reads the title for the rest). The seller's link (Tickchak,
hugim.org.il...) is kept as ``extra["purchase_url"]``; an event sold by a Smarticket box office the
smarticket source already reads is skipped. robots.txt only disallows ``/manager/``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from urllib.parse import unquote, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import get_settings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, jsonld_nodes, schema_types, to_decimal, zone
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.scrapers.smarticket import tenant_host

_EVENT_LINK = re.compile(r"page\.php\?type=event&(?:amp;)?id=(\d+)")
_ARCHIVE = "ארכיון אירועים"
# "add to calendar": dates=20261027T173000/20261027T193000 (the end is a default, not the site's)
_CAL_START = re.compile(r"dates=(\d{8})T(\d{2})(\d{2})")
_ICS_LOCATION = re.compile(r"LOCATION:(.*?)(?:%0A|\n|\\n)")
_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
_LABELLED = re.compile(r"(?:שעה|בשעות|משעה)\s*:?\s*(\d{1,2}):(\d{2})")
_UNTIL = re.compile(r"עד שעה\s*:?\s*(\d{1,2}):(\d{2})")
# The place field of each theme (the calendar file's LOCATION comes first).
_PLACE_FIELDS = (
    ".eic-place .eic-value",
    ".details-table.location .second-column",
    ".yellow-line span",
    ".class-text h6",
)
_PLACE_LABELS = ("מיקום:", "איפה?")


class MatnasnetSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://www.givatzeev.org.il  (the listing is <url>/page.php?type=events)
    city: str
    # Month pages read after the listing, for a calendar theme that lists only the current month.
    months: int = 0
    # Regional council: a place naming one of these localities is in that locality, not in `city`.
    localities: list[str] = Field(default_factory=list)


class MatnasnetSettings(SourceSettings):
    sites: list[MatnasnetSite] = Field(min_length=1)


def covered_smarticket_hosts() -> frozenset[str]:
    """Hosts of the Smarticket box offices the smarticket source reads (its ``tenants``)."""
    block = get_settings().sources.get("smarticket") or {}
    domain = block.get("tenant_domain", "smarticket.co.il")
    return frozenset(tenant_host(t["host"], domain) for t in block.get("tenants") or [])


class MatnasnetScraper(BaseScraper[MatnasnetSettings]):
    name = "matnasnet"
    settings_model = MatnasnetSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        skip = covered_smarticket_hosts()
        for site in self.settings.sites:
            try:
                async for event in self._site(site, skip):
                    yield event
            except SourceBlockedError as exc:  # one site's firewall never stops the others
                self.record_failure(site.url, exc)

    async def _site(self, site: MatnasnetSite, skip: frozenset[str]) -> AsyncIterator[ScrapedEvent]:
        ids: dict[str, None] = {}
        for page_url in listing_urls(site, datetime.now(zone(self.timezone)).date()):
            page = await self.optional(f"{page_url} listing", self.get_text(page_url))
            ids.update(dict.fromkeys(parse_listing(page or "")))
        self.log.info("%s: listed %d events", site.city, len(ids))

        async def fetch(event_id: str) -> ScrapedEvent | None:
            url = f"{site.url}/page.php?type=event&id={event_id}"
            return build_event(url, await self.get_text(url), site, self.timezone, skip)

        async for event in self.stream_isolated(ids, fetch, label=lambda i: f"{site.url} event {i}"):
            yield event


def listing_urls(site: MatnasnetSite, today: date) -> list[str]:
    urls = [f"{site.url}/page.php?type=events"]
    for ahead in range(1, site.months + 1):
        year, month = divmod(today.month - 1 + ahead, 12)
        urls.append(f"{site.url}/page.php?type=events&m={month + 1}&y={today.year + year}")
    return urls


def parse_listing(page_html: str) -> list[str]:
    """Event ids linked from the upcoming part of a listing (the archive below it is left out)."""
    return list(dict.fromkeys(_EVENT_LINK.findall(page_html.split(_ARCHIVE)[0])))


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def _place(tree: LexborHTMLParser, page_html: str) -> str | None:
    ics = _ICS_LOCATION.search(page_html)
    if ics and clean_str(unquote(ics.group(1))):
        return clean_str(unquote(ics.group(1)))
    for selector in _PLACE_FIELDS:
        found = _text(tree.css_first(selector))
        if found and not found.startswith("מחיר"):
            return found
    for node in tree.css("h4, .event_item"):
        found = _text(node)
        for label in _PLACE_LABELS:
            if found.startswith(label):
                return clean_str(found.removeprefix(label))
    return None


def _split_place(place: str | None) -> tuple[str | None, str | None]:
    """Venue and address of "בית המייסדים, רחוב גיבורי ישראל"."""
    if not place:
        return None, None
    venue, *address = re.split(r",\s+|\s+-\s+", place, maxsplit=1)
    return clean_str(venue), clean_str(address[0]) if address else None


def _seller_links(tree: LexborHTMLParser, base: str) -> list[str]:
    """Links of the "tickets" buttons and of an embedded Smarticket page; the site's own order page is not one."""
    links = [a.attributes.get("href") or "" for a in tree.css("a") if a.css_first(".bayBottun")]
    links += [f.attributes.get("src") or "" for f in tree.css("iframe#smarticket")]
    own = urlsplit(base).hostname
    absolute = (urljoin(base + "/", link) for link in links if link)
    return list(dict.fromkeys(u for u in absolute if u.startswith("http") and urlsplit(u).hostname != own))


def build_event(
    url: str, page_html: str, site: MatnasnetSite, timezone: str, skip_hosts: frozenset[str]
) -> ScrapedEvent | None:
    """One event page; None without an Event / a date, or when a covered Smarticket box office sells it."""
    nodes = jsonld_nodes(page_html)
    event = next((n for n in nodes if "event" in schema_types(n)), None)
    title = clean_str(event.get("name")) if event else None
    start_day = str(event.get("startDate") or "")[:10] if event else ""
    tree = LexborHTMLParser(page_html)
    for junk in tree.css("script, style, header, footer, nav"):
        junk.decompose()
    text = _text(tree.css_first("main") or tree.body)
    if not event or not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_day) or "אין תאריך" in text:
        return None
    links = _seller_links(tree, site.url)
    if any(urlsplit(link).hostname in skip_hosts for link in links):
        return None

    description = clean_str(str(event.get("description") or "").replace("\\n", "\n"))
    # Times are read above the description (which may quote other hours).
    head = text.split(description[:30])[0] if description else text
    on = date.fromisoformat(start_day)
    tz = zone(timezone)
    cal = _CAL_START.search(page_html)
    span = _RANGE.search(head)
    labelled = _LABELLED.search(head)
    start_clock = end_clock = None
    if cal and cal.group(1) == on.strftime("%Y%m%d") and cal.group(2) + cal.group(3) != "0000":
        start_clock = time(int(cal.group(2)) % 24, int(cal.group(3)))
    elif labelled:
        start_clock = time(int(labelled.group(1)) % 24, int(labelled.group(2)))
    elif span:
        start_clock = time(int(span.group(1)) % 24, int(span.group(2)))
    until = _UNTIL.search(head)
    if span:
        end_clock = time(int(span.group(3)) % 24, int(span.group(4)))
    elif until:
        end_clock = time(int(until.group(1)) % 24, int(until.group(2)))
    start = datetime.combine(on, start_clock or time(), tz)
    end = datetime.combine(on, end_clock, tz) if end_clock and start_clock else None

    place = _place(tree, page_html)
    venue, address = _split_place(place)
    city = next((loc for loc in site.localities if place and loc in place), site.city)
    offer = event.get("offers") if isinstance(event.get("offers"), dict) else {}
    price = to_decimal(offer.get("price"))
    org = next((n for n in nodes if "localbusiness" in schema_types(n)), {})
    image = clean_str(event.get("image"))
    if image and image in (org.get("logo"), org.get("image")):  # no flyer: the site falls back to its logo
        image = None
    tiers = [TicketTier(name="כרטיס", price=price, is_free=price == 0)] if price is not None else []
    found = _EVENT_LINK.search(url)
    host = urlsplit(site.url).hostname or ""
    return ScrapedEvent(
        source=MatnasnetScraper.name,
        source_event_id=f"{host.removeprefix('www.')}:{found.group(1) if found else url}",
        url=url,
        title=title,
        description=description,
        start_at=start,
        time_known=start_clock is not None,
        end_at=end,
        venue_name=venue,
        address=address,
        city=city,
        # Most themes show no tags: a community-centre event is filed under community then.
        source_genres=[t for t in (_text(n) for n in tree.css(".tags .tag")) if t] or ["קהילה"],
        organizer=clean_str(org.get("name")),
        image_url=image,
        min_price=price,
        max_price=price,
        tickets=tiers,
        extra={"purchase_url": links[0]} if links else {},
    )
