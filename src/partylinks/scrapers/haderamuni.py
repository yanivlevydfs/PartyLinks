"""Hadera municipality events (hadera.muni.il, post type ``muni_events``) - culture, sport, community.

WordPress with Elementor; robots.txt allows everything, the REST API answers 401 and the events page
loads its list through admin-ajax, so the core sitemap is the listing: ``/wp-sitemap.xml`` names
``wp-sitemap-posts-muni_events-<n>.xml`` (~790 pages all-time, each with ``<lastmod>``). Only pages
modified in the last ``lastmod_days`` are read (most of the sitemap is past events); the rest are
never requested. Each event page has no Event JSON-LD, but its Elementor blocks give:

- the title (``h2.elementor-heading-title``) and the post content (description, artwork, a line
  naming the hall: "באולם רנה שני", "מרכז ברוש (גן ברנדייס), חדרה");
- the post-info list: start "2 דצמבר, 18:30 , יום רביעי" and end "2 דצמבר, 20:00" (no year: it
  comes from a "02.12.2026" in the text, else the year that puts the day nearest the page's
  ``lastmod``), the audiences (``target_audience`` term links), the price "55 ₪" / "0 ₪";
- the buttons: Waze (``ll=<lat>,<lon>`` on some pages) and "לרכישת כרטיס" (the "פנאי העיר" box
  office pnay.org.il, a bit.ly, an association's form), kept as ``extra["purchase_url"]``;
- the map embed, whose title is the place's address ("האלון 28, חדרה").

The site's categories (``events_category``, ``event_interests``, ``event_location``) appear only as
post classes: ``events_category-401``, the term id when the Hebrew slug leaves nothing ASCII
(WordPress' ``sanitize_html_class``), else the slug's ASCII rest. They are named from the taxonomy
sitemaps (one request each, only for a taxonomy some event uses) and, for id classes, the term
archive pages (``term-<id>`` body class, ``<title>`` = the name), read only until every id the
events carry is named. Pages sold through the municipal Smarticket box office (``eventshadera``,
read by the smarticket source) are skipped.

Requests per run: 1 index + 1 posts sitemap + ~110 pages (120 days of lastmod) + a few taxonomy
sitemaps and term pages.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?")
_MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}  # fmt: skip
_DAY = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")(?:\s*,\s*(\d{1,2}):(\d{2}))?")
_FULL_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)")
_POST_ID = re.compile(r"\bpostid-(\d+)\b")
_TITLE_SUFFIX = re.compile(r"\s+[–-]\s+אתר האינטרנט\s*$")


class HaderaMuniSettings(SourceSettings):
    site_url: str  # https://www.hadera.muni.il
    city: str
    sitemap_path: str = "/wp-sitemap.xml"
    post_type: str = "muni_events"
    # Pages not modified for this long are past events (the site keeps every event it ever had).
    lastmod_days: int = Field(120, ge=1)
    taxonomies: list[str] = Field(default_factory=lambda: ["events_category", "event_interests", "event_location"])
    venue_taxonomy: str = "event_location"
    # Audiences (target_audience terms) that name a kind of event; the rest (adults, seniors...) do not.
    genre_audiences: list[str] = Field(default_factory=list)
    # Spellings of a hall in the texts -> its name ("רינה שני" -> "אולם רנה שני").
    venue_aliases: dict[str, str] = Field(default_factory=dict)
    # Halls the pages name without a street (substring of the venue -> street), for geocoding.
    venue_addresses: dict[str, str] = Field(default_factory=dict)
    # Box offices another source reads in full: their events are skipped here.
    skip_purchase_hosts: list[str] = Field(default_factory=list)
    place_words: list[str] = Field(
        default_factory=lambda: [
            "אולם", "מרכז", "מועדון", "ספריית", "הספרייה", "היכל", "בית ספר", "בית הספר", "פארק", "גן ",
            "תיאטרון", "מתנ\"ס", "קאנטרי", "בריכת", "כיכר", "רחבת", "מוזיאון", "אמפי", "חוף", "בית יד לבנים",
        ]
    )  # fmt: skip


class HaderaMuniScraper(BaseScraper[HaderaMuniSettings]):
    name = "haderamuni"
    settings_model = HaderaMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        maps = parse_sitemap(await self.get_text(s.site_url + s.sitemap_path))
        posts = [u for u, _ in maps if f"-posts-{s.post_type}-" in u] or [
            f"{s.site_url}/wp-sitemap-posts-{s.post_type}-1.xml"
        ]
        since = datetime.now(tz) - timedelta(days=s.lastmod_days)
        pages: dict[str, datetime] = {}
        for sitemap in posts:
            pages.update(recent_pages(await self.get_text(sitemap), since, self.timezone))
        self.log.debug("%d event pages modified since %s", len(pages), since.date())

        async def fetch(item: tuple[str, datetime]) -> tuple[ScrapedEvent, list[str]] | None:
            url, lastmod = item
            return parse_event(await self.get_text(url), url, lastmod, s, self.timezone)

        found = [r async for r in self.stream_isolated(pages.items(), fetch, label=lambda i: i[0])]
        retention = RetentionSettings()
        found = [
            (e, t)
            for e, t in found
            if is_current(e.start_at, e.end_at, retention, self.timezone, time_known=e.time_known)
        ]
        names = await self._term_names({t for _, tokens in found for t in tokens}, maps)
        for event, tokens in found:
            yield apply_terms(event, tokens, names, s)

    async def _term_names(self, needed: set[str], maps: list[tuple[str, str | None]]) -> dict[str, str]:
        """Post class (``events_category-401``) -> term name, for the classes the events carry."""
        names: dict[str, str] = {}
        for tax in self.settings.taxonomies:
            wanted = {t for t in needed if t.startswith(tax + "-")}
            if not wanted:
                continue
            by_id: list[str] = []  # term pages whose post class is the term id
            for sitemap in [u for u, _ in maps if f"-taxonomies-{tax}-" in u]:
                text = await self.optional(f"{tax} sitemap", self.get_text(sitemap))
                for url, _ in parse_sitemap(text or ""):
                    cls, name = term_class(url)
                    if cls:
                        names[f"{tax}-{cls}"] = name
                    else:
                        by_id.append(url)
            for url in by_id:
                if wanted <= names.keys():
                    break
                page = await self.optional(f"term {url}", self.get_text(url))
                if page and (term := parse_term_page(page)):
                    term_id, name = term
                    names[f"{tax}-{term_id}"] = name or term_class(url)[1]
        return names


def parse_sitemap(xml: str) -> list[tuple[str, str | None]]:
    """(loc, lastmod) of every entry of a sitemap or a sitemap index."""
    return [(loc.replace("&amp;", "&"), lastmod or None) for loc, lastmod in _LOC.findall(xml)]


def recent_pages(xml: str, since: datetime, timezone: str) -> dict[str, datetime]:
    """Event pages modified at ``since`` or later, with their lastmod; undated pages are kept."""
    pages: dict[str, datetime] = {}
    for url, lastmod in parse_sitemap(xml):
        when = parse_iso(lastmod, timezone) if lastmod else None
        if when is None or when >= since:
            pages[url] = when or since
    return pages


def term_class(url: str) -> tuple[str | None, str]:
    """A term archive URL -> (the post class WordPress gives it, or None when it is the term id; the name)."""
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    cls = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"%[a-fA-F0-9]{2}", "", slug))
    name = unquote(slug).replace("-", " ").strip()
    if cls.lstrip("-").isdigit() or not cls.strip("-"):
        return None, name
    return cls, name


def parse_term_page(page_html: str) -> tuple[str, str | None] | None:
    """A term archive page -> (term id, the name in its <title>)."""
    tree = LexborHTMLParser(page_html)
    body = tree.css_first("body")
    classes = (body.attributes.get("class") or "").split() if body else []
    term_id = next((c[5:] for c in classes if c.startswith("term-") and c[5:].isdigit()), None)
    if not term_id:
        return None
    title = _text(tree.css_first("title"))
    return term_id, _TITLE_SUFFIX.sub("", title).strip() or None


def apply_terms(event: ScrapedEvent, tokens: list[str], names: dict[str, str], s: HaderaMuniSettings) -> ScrapedEvent:
    """The event with its named categories as genres; its event_location term as the venue."""
    update: dict[str, object] = {}
    genres = list(event.source_genres)
    for token in tokens:
        name = names.get(token)
        if not name:
            continue
        if token.startswith(s.venue_taxonomy + "-"):
            venue = _alias(name, s)
            update["venue_name"] = venue
            if not event.address:
                update["address"] = _street(venue, s)
        elif name.lower() not in genres:
            genres.append(name.lower())
    update["source_genres"] = genres
    return event.model_copy(update=update)


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def _alias(venue: str, s: HaderaMuniSettings) -> str:
    return next((name for key, name in s.venue_aliases.items() if key in venue), venue)


def _street(venue: str | None, s: HaderaMuniSettings) -> str | None:
    return next((a for key, a in s.venue_addresses.items() if venue and key in venue), None)


def _year(day: int, month: int, text: str, lastmod: datetime) -> int:
    """The year of a day written without one: a full date in the text, else the nearest to ``lastmod``."""
    for d, m, y in _FULL_DATE.findall(text):
        if (int(d), int(m)) == (day, month):
            return int(y)
    ref = lastmod.date()
    years = [y for y in (ref.year - 1, ref.year, ref.year + 1) if _valid(y, month, day)]
    return min(years, key=lambda y: (abs((date(y, month, day) - ref).days), -y))


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _place_line(paragraphs: list[str], s: HaderaMuniSettings) -> str | None:
    """The content line naming the hall: "באולם רנה שני", "מרכז ברוש (גן ברנדייס), חדרה"."""
    for line in paragraphs:
        if len(line) > 70 or ":" in line or "₪" in line:
            continue
        text = line
        if not any(text.startswith(w) for w in s.place_words) and text.startswith("ב"):
            text = text[1:]
        if any(text.startswith(w) for w in s.place_words):
            text = re.sub(r"\s*\([^)]*\)", "", text)
            text = text.removesuffix(", " + s.city).removesuffix(" " + s.city).strip(" ,.")
            return text or None
    return None


def _clean_address(value: str | None, city: str) -> str | None:
    """The map embed's title: "האלון 28, חדרה" -> "האלון 28"; a name without a number is no address."""
    text = clean_str(value)
    if not text or not any(c.isdigit() for c in text):
        return None
    text = text.removesuffix(", " + city).removeprefix(city + " ").strip(" ,")
    return text or None


def _waze_point(url: str | None) -> tuple[float, float] | None:
    ll = (parse_qs(urlsplit(url or "").query).get("ll") or [""])[0]
    lat, _, lon = ll.partition(",")
    try:
        point = float(lat), float(lon)
    except ValueError:
        return None
    return point if 29 < point[0] < 34 and 34 < point[1] < 36 else None


def parse_event(
    page_html: str, url: str, lastmod: datetime, s: HaderaMuniSettings, timezone: str
) -> tuple[ScrapedEvent, list[str]] | None:
    """The page's event and its taxonomy post classes; None when undated or sold by a skipped box office."""
    tree = LexborHTMLParser(page_html)
    root = tree.css_first('[data-elementor-type="single-post"]')
    if root is None:
        raise ParseError(f"{url}: no event block")
    body = tree.css_first("body")
    post_id = _POST_ID.search(body.attributes.get("class") or "") if body else None
    title = _text(root.css_first("h2.elementor-heading-title")) or _TITLE_SUFFIX.sub("", _text(tree.css_first("title")))
    if not post_id or not title:
        raise ParseError(f"{url}: no post id or title")
    content = root.css_first(".elementor-widget-theme-post-content")
    paragraphs = [_text(p) for p in content.css("p")] if content else []
    page_text = " ".join(paragraphs)

    # The post-info list: dates (calendar icon), audiences (term links), price (payment icon).
    days: list[tuple[int, int, int | None, int | None]] = []
    price: Decimal | None = None
    audiences: list[str] = []
    for item in root.css(".elementor-post-info li"):
        icon = item.css_first(".elementor-icon-list-icon i")
        kind = (icon.attributes.get("class") or "") if icon else ""
        line = _text(item.css_first(".elementor-icon-list-text"))
        if "calendar" in kind and (m := _DAY.search(line)):
            d, month, hh, mm = m.groups()
            days.append((int(d), _MONTHS[month], int(hh) if hh else None, int(mm) if mm else None))
        elif "payment" in kind and (p := _PRICE.search(line)):
            price = to_decimal(p.group(1))
        audiences += [_text(a) for a in item.css("a.elementor-post-info__terms-list-item")]
    if not days:
        return None
    tz = zone(timezone)
    d, month, hh, mm = days[0]
    year = _year(d, month, page_text, lastmod)
    known = hh is not None
    start = datetime.combine(date(year, month, d), time(hh % 24, mm or 0) if known else time(), tz)
    end = None
    ed, emonth, ehh, emm = days[1] if len(days) > 1 else (0, 0, None, None)
    eyear = year + 1 if (emonth, ed) < (month, d) else year
    if known and ehh is not None and _valid(eyear, emonth, ed):
        end = datetime.combine(date(eyear, emonth, ed), time(ehh % 24, emm or 0), tz)
        if not start < end <= start + timedelta(days=1):
            end = None  # a season's last day or a placeholder, not this show's end

    buttons = root.css("a.ha-creative-btn")
    waze = next((b.attributes.get("href") for b in buttons if "waze.com" in (b.attributes.get("href") or "")), None)
    purchase = next(
        (
            href
            for b in buttons
            if (href := clean_str(b.attributes.get("href"))) and href.startswith("http") and "waze.com" not in href
        ),
        None,
    )
    if not purchase and content:  # "להזמנת כרטיסים לחצו כאן" in the text
        purchase = next(
            (
                href
                for a in content.css("p a[href]")
                if (href := clean_str(a.attributes.get("href")))
                and href.startswith("http")
                and "/wp-content/" not in href
            ),
            None,
        )
    host = urlsplit(purchase or "").netloc.lower()
    if host and any(host == h or host.endswith("." + h) for h in s.skip_purchase_hosts):
        return None

    frame = root.css_first(".elementor-widget-google_maps iframe")
    map_title = clean_str(frame.attributes.get("title")) if frame else None
    venue = _place_line(paragraphs, s) or (map_title.split(",")[0].strip() if map_title else None)
    venue = _alias(venue, s) if venue else None
    address = _clean_address(map_title, s.city) or _street(venue, s)
    point = _waze_point(waze)

    art = content.css_first("figure a[href]") if content else None
    featured = root.css_first(".elementor-widget-theme-post-featured-image img")
    image = clean_str(art.attributes.get("href")) if art else None
    image = image or (clean_str(featured.attributes.get("src")) if featured else None)
    tiers = (
        [TicketTier(name="כרטיס" if price else "כניסה חופשית", price=price, is_free=price == 0)]
        if price is not None
        else []
    )
    if content:
        for fig in content.css("figure"):
            fig.decompose()
    event = ScrapedEvent(
        source=HaderaMuniScraper.name,
        source_event_id=post_id.group(1),
        url=url,
        title=title,
        description=html_to_text(content.html) if content else None,
        start_at=start,
        end_at=end,
        time_known=known,
        venue_name=venue,
        address=address,
        city=s.city,
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        source_genres=[a.lower() for a in dict.fromkeys(audiences) if a in s.genre_audiences],
        organizer=f"עיריית {s.city}",
        image_url=image if image and image.startswith("http") else None,
        min_price=price,
        max_price=price,
        tickets=tiers,
        extra={"purchase_url": purchase} if purchase else {},
    )
    classes = (root.attributes.get("class") or "").split()
    tokens = [c for c in classes if any(c.startswith(t + "-") for t in s.taxonomies)]
    return event, tokens
