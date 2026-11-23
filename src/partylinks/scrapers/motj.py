"""Museum of Tolerance Jerusalem (motj.org.il, "מוזיאון הסובלנות ירושלים"), Hillel 32 - its concerts, shows,
plays, lectures, stand-up nights, children's shows and holiday evenings.

The site is WordPress. Its ``event`` post type is in wp-json (``events_path``, 100 a page, every page: ~140 posts,
most past) with ACF fields: the day (``acf.date``, ``YYYYMMDD``), the lines under the title (``acf.meta``: the day,
"פתיחת דלתות: 19:30 | תחילת המופע: 20:30", the place), the text (``acf.description``, ``good_to_know``) and the
ticket link (``acf.external_link``). The ``/en/`` posts are WPML copies of the same events, so only Hebrew is read.
Posts of ``acf.type`` "archive" are write-ups of past evenings. The kind ("הרצאה", "מופע", "הצגה", "לכל
המשפחה") comes from the title and the clock line (``kinds``): the site has no categories.

Most shows sell through Castilia (castilia.co.il, a Smarticket white label): its museum page (``castilia_listing``,
``/he/ShowType/15``) lists every show the museum sells there, and each show page (``/he/Show/<id>``) lists its
performances (``data-order-link`` "eventId=<n>", the day, the clock, the hall). Every performance is one event;
its ticket link is the Smarticket page of that ``eventId`` (``castilia.smarticket.co.il/event/<n>``), so dedup
joins it with the Smarticket ``castilia`` tenant. A show on Castilia with no post on the site is read from Castilia
alone. Links to the museum's own box office (tickets.motj.org.il, Smarticket ``motj``) are sent under its
Smarticket host. Requests per run: the wp-json pages (2), the Castilia listing and one page per coming show (~8).
Every event is at the museum: venue, address and point come from the config. robots.txt allows everything on
both sites (Castilia disallows only /admin, /basket and /checkout).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets

_ACF_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
# "תחילת המופע: 20:30", "תחילת מופע ב-21:00", "תחילת ההרצאה 20:00": the start (the doors open before it).
_START = re.compile(r"תחילת[^\d|]{0,20}?([01]?\d|2[0-3]):([0-5]\d)")
_RANGE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)\s*[–\-]\s*([01]?\d|2[0-3]):([0-5]\d)")
# "13–14 באוקטובר 2026": one post for consecutive days.
_DAY_SPAN = re.compile(r"(?<!\d)(\d{1,2})\s*[–\-]\s*(\d{1,2})(?![\d.])")
_DMY = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{2}|20\d{2})(?![\d])")
_SHOW_ID = re.compile(r"/Show/(\d+)", re.IGNORECASE)
_ORDER_ID = re.compile(r"eventId=(\d+)", re.IGNORECASE)
_SMARTICKET_ID = re.compile(r"(?:/event/|[?&]id=)(\d+)")


class MotjSettings(SourceSettings):
    site_url: str = "https://motj.org.il"
    events_path: str = "/wp-json/wp/v2/event"
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(50, ge=1)  # a known total reads every page; this only bounds a broken total
    fields: str = "id,link,title,acf,yoast_head_json"
    castilia_url: str = "https://castilia.co.il"
    castilia_listing: str = "/he/ShowType/15"  # the museum's page on Castilia
    # Castilia's eventId is the Smarticket performance id of this tenant.
    castilia_box_office: str = "castilia.smarticket.co.il"
    # A ticket host -> the Smarticket host the same tenant is read under (dedup joins the two listings).
    box_office_aliases: dict[str, str] = Field(default_factory=lambda: {"tickets.motj.org.il": "motj.smarticket.co.il"})
    # Hosts whose link is the event's ticket page (a video or a social post is not).
    ticket_hosts: list[str] = Field(
        default_factory=lambda: ["castilia.co.il", "smarticket.co.il", "tickets.motj.org.il", "eventer.co.il"]
    )
    # Our label -> words of the title or the clock line ("תחילת ההרצאה") that name it: the site has no categories.
    kinds: dict[str, list[str]] = Field(default_factory=dict)
    # Words of a performance's place on Castilia that name a session of its own ("מוזיאון הסובלנות ירושלים,
    # סדנת הקול הפנימי עם אורקה": the workshop before the evening show), which then is its title.
    session_words: list[str] = Field(default_factory=lambda: ["סדנ"])
    # Words of a title that is no public event (a cancelled date, a notice, a test post, a broadcast).
    skip_words: list[str] = Field(default_factory=lambda: ["בוטל", "מבוטל"])
    # A postponed show keeps its title behind a notice: "האירוע נדחה ל-24.12 | מעגלים".
    notice_prefix: str = r"^\s*(?:האירוע|ההופעה|המופע|ההרצאה)\s+(?:נדחה|נדחתה|נקבע|נקבעה|הוקדם|הוקדמה)[^|]*\|\s*"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Performance:
    event_id: str
    day: date
    clock: time | None
    place: str | None


@dataclass
class CastiliaShow:
    show_id: str
    title: str | None = None
    image: str | None = None
    url: str | None = None
    description: str | None = None
    performances: list[Performance] = field(default_factory=list)


class MotjScraper(BaseScraper[MotjSettings]):
    name = "motj"
    settings_model = MotjSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        today = datetime.now(tz).date()

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_fields": s.fields}
            response = await self.request("GET", s.site_url + s.events_path, params=params)
            rows = response.json()
            return ([r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []), response.headers.get(
                "X-WP-Total"
            )

        posts = [p for p in await self.paginate(page, s.per_page, s.max_pages) if coming_post(p, today)]
        listing = await self.optional("castilia listing", self.get_text(s.castilia_url + s.castilia_listing))
        shows = {show.show_id: show for show in parse_castilia_listing(listing or "", s)}
        for post in posts:  # a show linked from a coming post, even if the listing left it out
            if (show_id := castilia_show_id(post)) and show_id not in shows:
                shows[show_id] = CastiliaShow(show_id=show_id, url=_castilia_link(post))
        for show in shows.values():
            html = await self.optional(f"castilia show {show.show_id}", self.get_text(show.url or ""))
            if html:
                parse_castilia_show(html, show, s)
        self.log.debug("%d coming posts, %d castilia shows", len(posts), len(shows))

        events: list[ScrapedEvent] = []
        linked: set[str] = set()
        for post in posts:
            show = shows.get(castilia_show_id(post) or "")
            if show is not None:
                linked.add(show.show_id)
            try:
                events += post_events(post, show, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"event {post.get('id')}", exc)
        for show in shows.values():
            if show.show_id not in linked:
                events += castilia_events(show, s, self.timezone)
        current = [
            e
            for e in events
            if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone, time_known=e.time_known)
        ]
        for event in sorted(current, key=lambda e: (e.start_at, e.source_event_id)):
            yield event  # nearest first


def coming_post(post: dict[str, Any], today: date) -> bool:
    """A full event post whose day (or the day after, for a two-day post) is not past."""
    acf = post.get("acf") if isinstance(post.get("acf"), dict) else {}
    day = _acf_day(acf.get("date"))
    return acf.get("type") != "archive" and day is not None and day >= today - timedelta(days=1)


def castilia_show_id(post: dict[str, Any]) -> str | None:
    link = _castilia_link(post)
    found = _SHOW_ID.search(link or "")
    return found.group(1) if found else None


def _castilia_link(post: dict[str, Any]) -> str | None:
    link = _external_link(post)
    return link if link and (urlparse(link).hostname or "").removeprefix("www.") == "castilia.co.il" else None


def _external_link(post: dict[str, Any]) -> str | None:
    acf = post.get("acf") if isinstance(post.get("acf"), dict) else {}
    ext = acf.get("external_link")
    url = clean_str(ext.get("url")) if isinstance(ext, dict) else None
    return url if url and url.startswith("http") else None


def parse_castilia_listing(html: str, s: MotjSettings) -> list[CastiliaShow]:
    """The museum's Castilia page -> its shows (id, name, artwork, page)."""
    shows: list[CastiliaShow] = []
    for node in LexborHTMLParser(html).css(".events-grid a[href]"):
        href = (node.attributes.get("href") or "").strip()
        found = _SHOW_ID.search(href)
        if not found:
            continue
        name = node.css_first(".artist-name")
        image = node.css_first("img[src]")
        shows.append(
            CastiliaShow(
                show_id=found.group(1),
                title=clean_str(name.text()) if name is not None else None,
                image=_absolute(s.castilia_url, image.attributes.get("src")) if image is not None else None,
                url=urljoin(s.castilia_url + "/", href),
            )
        )
    return list({show.show_id: show for show in shows}.values())


def parse_castilia_show(html: str, show: CastiliaShow, s: MotjSettings) -> None:
    """A Castilia show page -> its performances (and the banner and text for a show without a post)."""
    tree = LexborHTMLParser(html)
    seen: set[str] = set()
    for node in tree.css("#section-show-events a[data-order-link]"):
        found = _ORDER_ID.search(node.attributes.get("data-order-link") or "")
        day_node = node.css_first(".show-date")
        day = _dmy(day_node.text()) if day_node is not None else None
        if not found or day is None or found.group(1) in seen:
            continue
        seen.add(found.group(1))
        when = node.css_first(".show-date-text")
        clock = _CLOCK.search(when.text()) if when is not None else None
        place = node.css_first(".show-place")
        show.performances.append(
            Performance(
                event_id=found.group(1),
                day=day,
                clock=time(int(clock.group(1)), int(clock.group(2))) if clock else None,
                place=clean_str(place.text()) if place is not None else None,
            )
        )
    banner = tree.css_first(".show-banner img[src]")
    if banner is not None:
        show.image = _absolute(s.castilia_url, banner.attributes.get("src")) or show.image
    content = tree.css_first("#section-show-content")
    if content is not None and content.html:
        text = html_to_text(content.html) or ""
        show.description = clean_str(text.replace("לרכישת כרטיסים", "", 1)) or None


def post_events(post: dict[str, Any], show: CastiliaShow | None, s: MotjSettings, timezone: str) -> list[ScrapedEvent]:
    """One event post -> its events: one per Castilia performance, else one per day of the post."""
    acf = post.get("acf") if isinstance(post.get("acf"), dict) else {}
    title = clean_title(str((post.get("title") or {}).get("rendered") or ""), s)
    day = _acf_day(acf.get("date"))
    if not post.get("id") or not post.get("link") or not title or day is None:
        return []
    if any(w in title for w in s.skip_words):
        return []
    tz = zone(timezone)
    lines = [clean_str(unescape(str(m.get("text") or ""))) or "" for m in acf.get("meta") or [] if isinstance(m, dict)]
    clock_line = " | ".join(lines[1:]) if lines else ""
    description = "\n\n".join(
        t for t in (html_to_text(str(acf.get(k) or "")) for k in ("description", "good_to_know")) if t
    ) or clean_str(acf.get("short_description"))
    tickets = text_tickets(description or "")
    prices = [t.price for t in tickets if t.price is not None]
    og = (post.get("yoast_head_json") or {}).get("og_image") or []
    images = [str(i["url"]) for i in og if isinstance(i, dict) and str(i.get("url") or "").startswith("http")]
    genres = kinds(f"{title} {clock_line}", s) or kinds(clean_str(acf.get("short_description")) or "", s)
    base: dict[str, Any] = {
        "source": MotjScraper.name,
        "url": str(post["link"]),
        "title": title,
        "description": description,
        "venue_name": s.venue_name,
        "address": s.address,
        "city": s.city,
        "latitude": s.latitude,
        "longitude": s.longitude,
        "source_genres": genres,
        "organizer": s.venue_name,
        "images": images,
        "tickets": tickets,
        "min_price": min(prices, default=None),
        "max_price": max(prices, default=None),
    }
    if show is not None and show.performances:
        events = []
        for p in show.performances:
            event = ScrapedEvent(
                **base,
                source_event_id=f"{post['id']}-{p.event_id}",
                start_at=datetime.combine(p.day, p.clock or time.min, tzinfo=tz),
                time_known=p.clock is not None,
                extra={"purchase_url": f"https://{s.castilia_box_office}/event/{p.event_id}"},
            )
            if name := session_name(p.place, s):  # the workshop before the show: a title of its own
                event = event.model_copy(update={"title": name, "source_genres": kinds(name, s) or genres})
            events.append(event)
        return events
    start, end = post_clock(" | ".join(lines))
    extra: dict[str, Any] = {}
    if link := ticket_link(_external_link(post), s):
        extra["purchase_url"] = link
    days = post_days(day, lines[0] if lines else "")
    return [
        ScrapedEvent(
            **base,
            source_event_id=str(post["id"]) if len(days) == 1 else f"{post['id']}-{d:%Y%m%d}",
            start_at=datetime.combine(d, start or time.min, tzinfo=tz),
            end_at=datetime.combine(d, end, tzinfo=tz) if start and end and end > start else None,
            time_known=start is not None,
            extra=dict(extra),
        )
        for d in days
    ]


def castilia_events(show: CastiliaShow, s: MotjSettings, timezone: str) -> list[ScrapedEvent]:
    """A museum show on Castilia with no post on the site: its performances from Castilia alone."""
    title = clean_title(show.title or "", s)
    if not title or not show.url or any(w in title for w in s.skip_words):
        return []
    tz = zone(timezone)
    tickets = text_tickets(show.description or "")
    prices = [t.price for t in tickets if t.price is not None]
    return [
        ScrapedEvent(
            source=MotjScraper.name,
            source_event_id=f"castilia-{p.event_id}",
            url=show.url,
            title=title,
            description=show.description,
            start_at=datetime.combine(p.day, p.clock or time.min, tzinfo=tz),
            time_known=p.clock is not None,
            venue_name=s.venue_name,
            address=s.address,
            city=s.city,
            latitude=s.latitude,
            longitude=s.longitude,
            source_genres=kinds(title, s) or kinds(show.description or "", s),
            organizer=s.venue_name,
            images=[show.image] if show.image else [],
            tickets=tickets,
            min_price=min(prices, default=None),
            max_price=max(prices, default=None),
            extra={"purchase_url": f"https://{s.castilia_box_office}/event/{p.event_id}"},
        )
        for p in show.performances
    ]


def session_name(place: str | None, s: MotjSettings) -> str | None:
    """The part of a performance's place that names a session of its own ("סדנת הקול הפנימי עם אורקה")."""
    for part in (place or "").split(","):
        if (name := clean_str(part)) and any(w in name for w in s.session_words):
            return name
    return None


def clean_title(raw: str, s: MotjSettings) -> str:
    title = clean_str(unescape(raw).replace("\xa0", " ")) or ""
    return clean_str(re.sub(s.notice_prefix, "", title)) or ""


def kinds(text: str, s: MotjSettings) -> list[str]:
    """Our labels for the words of ``text`` (``kinds``), lower case."""
    return [label.lower() for label, words in s.kinds.items() if any(w in text for w in words)]


def post_clock(text: str) -> tuple[time | None, time | None]:
    """The start and end in a post's lines: "תחילת המופע: 20:30", a range "18:30–19:30", else the first clock."""
    if found := _START.search(text):
        return time(int(found.group(1)), int(found.group(2))), None
    if found := _RANGE.search(text):
        return time(int(found.group(1)), int(found.group(2))), time(int(found.group(3)), int(found.group(4)))
    if found := _CLOCK.search(text):
        return time(int(found.group(1)), int(found.group(2))), None
    return None, None


def post_days(day: date, line: str) -> list[date]:
    """The post's day, and the days after it a span names ("שלישי–רביעי | 13–14 באוקטובר": two days)."""
    text = _DMY.sub(" ", line)
    found = _DAY_SPAN.search(text)
    if found and int(found.group(1)) == day.day and 0 < int(found.group(2)) - day.day <= 6:
        return [day + timedelta(days=n) for n in range(int(found.group(2)) - day.day + 1)]
    return [day]


def ticket_link(url: str | None, s: MotjSettings) -> str | None:
    """A ticket seller's link (``ticket_hosts``), the museum's box office under its Smarticket host."""
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    if not any(host == h or host.endswith("." + h) for h in s.ticket_hosts):
        return None
    if alias := s.box_office_aliases.get(host):
        found = _SMARTICKET_ID.search(url)
        return f"https://{alias}/event/{found.group(1)}" if found else parsed._replace(netloc=alias).geturl()
    return url


def _acf_day(value: Any) -> date | None:
    found = _ACF_DAY.match(str(value or "").strip())
    if not found:
        return None
    try:
        return date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError:
        return None


def _dmy(text: str) -> date | None:
    found = _DMY.search(text or "")
    if not found:
        return None
    year = int(found.group(3))
    try:
        return date(year + 2000 if year < 100 else year, int(found.group(2)), int(found.group(1)))
    except ValueError:
        return None


def _absolute(base: str, src: str | None) -> str | None:
    """An image path on Castilia ("/cache/w_1110/העבריות 3083X1000.png") -> a full, quoted URL."""
    src = (src or "").strip()
    if not src:
        return None
    url = urljoin(base + "/", src)
    parsed = urlparse(url)
    return (
        parsed._replace(path=quote(parsed.path, safe="/%,_.-")).geturl() if parsed.scheme in ("http", "https") else None
    )
