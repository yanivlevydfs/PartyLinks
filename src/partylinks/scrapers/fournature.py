"""Kehila Lemaan Hateva (4nature.org.il, "קהילה למען הטבע") - a Herzliya volunteers' community for urban
nature: free community meetings with a lecture, night tours in Herzliya Park, tree and bee tours, a course.

WordPress + Elementor, no events plugin: an event is a block of free text. Read every run:

- the home page (``home_path``): its "סיורים ואירועי קהילה" section is where coming meetings are announced
  (the wasp lecture of 7.12.26 is on no other page);
- every post (``posts_path``, the wp-json posts, paginated to the end): the site files its events as posts
  under /events/ (two categories: upcoming / past events);
- the listing pages (``listing_paths``: /events/ and both category pages) for any /events/ link no post lists.

Each Elementor container holding a date (the innermost one: its heading, text, sign-up link and picture) is
one event. The date is the block's first ``D.M.YY`` / ``D.M`` / ``D/M/YY`` that is not a rain date
("תאריך חלופי"); a date without a year takes the first year that puts it on or after the page's own date
(the post's publish date, the home page's last change) less ``yearless_grace_days``. The hour follows the
date ("משעה 19:00", "בשעה 09:00-11:00"); without one the event is date-only. The place is one of the known
``places`` named in the block (pinned), else the town only. The sign-up link (a Google Form) is the
purchase link and the event's link (our button sends the visitor to it; else the event's own page); "ללא תשלום" / "חינם" makes it free. A block with no picture of its own takes its page's
og:image. The site has no prices; its two categories (upcoming / past) are no genres, so the genre comes from
the words the title (else the text) uses (``kinds``).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, og_image, parse_iso, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class Place(BaseModel):
    match: list[str]  # any of these words in the block's text names the place
    venue_name: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class FourNatureSettings(SourceSettings):
    site_url: str = "https://www.4nature.org.il"
    home_path: str = "/"
    home_api_path: str = "/wp-json/wp/v2/pages/8?_fields=modified"  # the home page's last change
    posts_path: str = "/wp-json/wp/v2/posts"
    per_page: int = 50
    max_pages: int = 50
    listing_paths: list[str] = Field(
        default_factory=lambda: ["/events/", "/events/category/events-future/", "/events/category/events-past/"]
    )
    event_link: str = r"^https?://(?:www\.)?4nature\.org\.il/events/(?!category/)[^/?#]+/?$"
    city: str = "הרצליה"
    places: list[Place] = Field(default_factory=list)
    yearless_grace_days: int = 30
    # Home page sections whose undated blocks (a heading and a sign-up / details button, no date: the course, the
    # trainings for organisations) are listed as undated events (the owner's call, 24.11.2026).
    undated_sections: list[str] = Field(default_factory=lambda: ["סיורים ואירועי קהילה", "בית ספר לטבע"])
    # /events/ (``listing_paths[0]``): its undated offers are the sections between these two headings.
    listing_future: str = "אירועים עתידיים"
    listing_past: str = "אירועים קודמים"
    # Pages that are catalogues of courses booked on request (teva-city: six courses for organisations, each a
    # heading, a picture, "פירוט המפגשים" and a "לפרטים והזמנה" button to the page's order form): one undated
    # event per course. Read from the post pages already fetched.
    catalogue_paths: list[str] = Field(default_factory=lambda: ["/events/teva-city/"])
    catalogue_kind: str = "קורס"  # their source genre when the text names none
    rain_date_words: list[str] = Field(default_factory=lambda: ["חלופי", "חלופית"])
    # Words just before a post's own date ("יום ב' 8.7.24", "פתיחת הקורס: 5.9.25"), not a date it mentions.
    date_cues: list[str] = Field(
        default_factory=lambda: ["יום", "ביום", "פתיחת", "תאריך", "שריינו", "יתקיים", "תתקיים", "מועד"]
    )
    signup_words: list[str] = Field(default_factory=lambda: ["הרשמה", "להרשמה", "הרשמה ופרטים"])
    free_words: list[str] = Field(default_factory=lambda: ["ללא תשלום", "חינם", "ללא עלות"])
    # Pictures that are the site's decoration, not an event's.
    skip_images: list[str] = Field(default_factory=lambda: ["logo", "bg-graphics", "-bg.", ".svg"])
    # What an event is (its source genre), from the words its title uses, else its text. The site's own two
    # categories are "upcoming" / "past", not genres.
    kinds: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "הרצאה": ["הרצאה", "הרצאות", "ערב טבע"],
            "סיור": ["סיור", "סיורים", "טיול"],
            "קורס": ["קורס", "הכשרה", "הכשרות"],
            "תערוכת צילום": ["תערוכת צילום", "תערוכת הצילום", "תחרות צילום"],
        }
    )
    # The site gives a start hour only ("משעה 19:00"): a meeting's usual length, for its end.
    default_duration_minutes: int = 120


DATE = re.compile(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d:/]|\.\d)")
HOURS = re.compile(r"(?:שעה|בשעות|שעות)\s*:?\s*(\d{1,2}):(\d{2})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?")
AGE = re.compile(r"(?:מגיל|מעל גיל|לבני)\s*(\d{1,2})")


@dataclass
class Page:
    url: str
    html: str
    written: date  # the page's own date: yearless dates fall on or after it


class FourNatureScraper(BaseScraper[FourNatureSettings]):
    name = "fournature"
    settings_model = FourNatureSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        pages: list[Page] = []

        home_url = urljoin(s.site_url, s.home_path)
        home = await self.optional("home page", self.get_text(home_url))
        meta = await self.optional("home page date", self.get_json(s.site_url + s.home_api_path, expect=dict))
        changed = parse_iso(str((meta or {}).get("modified") or ""), self.timezone)
        if home:
            pages.append(Page(home_url, home, changed.date() if changed else today))

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_fields": "id,date,link"}
            response = await self.request("GET", s.site_url + s.posts_path, params=params)
            rows = response.json()
            rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
            return rows, response.headers.get("X-WP-Total")

        posts = await self.optional("posts", self.paginate(page, s.per_page, s.max_pages)) or []
        written: dict[str, date] = {}
        for post in posts:
            link = str(post.get("link") or "")
            day = parse_iso(str(post.get("date") or ""), self.timezone)
            if link:
                written[link.rstrip("/") + "/"] = day.date() if day else today

        links = set(written)
        listings = [
            await self.optional(f"listing {p}", self.get_text(urljoin(s.site_url, p))) or "" for p in s.listing_paths
        ]
        for html in [home or "", *listings]:
            links.update(event_links(html, s))

        async def fetch(url: str) -> Page:
            return Page(url, await self.get_text(url), written.get(url, today))

        async for found in self.stream_isolated(sorted(links), fetch, lambda u: f"page {u}"):
            pages.append(found)

        events: dict[str, ScrapedEvent] = {}
        for p in pages:
            try:
                parsed = parse_page(p, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad page must not cost the others
                self.record_failure(f"page {p.url}", exc)
                continue
            for event in parsed:  # the home page's desktop and mobile copies of one block are one event
                kept = events.get(event.source_event_id)
                if kept is not None:
                    images = list(dict.fromkeys([*kept.images, *event.images]))
                    if len(event.description or "") > len(kept.description or ""):
                        kept = event
                    event = kept.model_copy(update={"images": images})
                events[event.source_event_id] = event
        dated = [
            e
            for e in events.values()
            if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone, time_known=e.time_known)
        ]
        # The undated offers (the home page's sections, /events/'s coming events), unless their page has a coming
        # date; one offer named on both pages is one event (by its link).
        seen = {str(e.url) for e in dated} | {str(e.extra.get("purchase_url")) for e in dated}
        others = {q.url: q.html for q in pages}
        offers = undated(pages[0], others, s) if home else []
        if listings and listings[0]:
            offers += undated_listing(Page(urljoin(s.site_url, s.listing_paths[0]), listings[0], today), others, s)
        for path in s.catalogue_paths:
            url = urljoin(s.site_url, path)
            if html := others.get(url):
                offers += undated_catalogue(Page(url, html, today), s)
        for event in offers:
            if event.extra["page"] not in seen:  # a catalogue's courses share one form: each has its own "page" key
                seen.add(event.extra["page"])
                events.setdefault(event.source_event_id, event)
        for event in sorted(
            events.values(), key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))
        ):
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def event_links(html: str, s: FourNatureSettings) -> set[str]:
    """The /events/<slug>/ pages a page links to."""
    pattern = re.compile(s.event_link)
    found = set()
    for a in LexborHTMLParser(html).css("a[href]"):
        href = urljoin(s.site_url, a.attributes.get("href") or "").split("#")[0]
        if pattern.match(href):
            found.add(href.rstrip("/") + "/")
    return found


def blocks(tree: LexborHTMLParser) -> list[LexborNode]:
    """The innermost Elementor containers whose text holds a date: one event each."""
    dated = [n for n in tree.css(".e-con, .elementor-section") if DATE.search(n.text(separator=" "))]
    return [  # lexbor's css() includes the node itself
        n
        for n in dated
        if not any(c != n and DATE.search(c.text(separator=" ")) for c in n.css(".e-con, .elementor-section"))
    ]


def event_date(text: str, written: date, s: FourNatureSettings) -> tuple[date, re.Match[str]] | None:
    for m in DATE.finditer(text):
        if any(w in text[max(0, m.start() - 20) : m.start()] for w in s.rain_date_words):
            continue
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        try:
            if year:
                return date(int(year) + (2000 if len(year) == 2 else 0), month, day), m
            floor = written - timedelta(days=s.yearless_grace_days)
            for y in (floor.year, floor.year + 1):
                if (found := date(y, month, day)) >= floor:
                    return found, m
        except ValueError:
            continue
    return None


def kinds(title: str, text: str, s: FourNatureSettings) -> list[str]:
    """The event's kinds named in its title, else in its text."""
    for where in (title, text):
        if found := [kind for kind, words in s.kinds.items() if any(w in where for w in words)]:
            return found
    return []


def parse_page(page: Page, s: FourNatureSettings, timezone: str) -> list[ScrapedEvent]:
    """The home page: one event per dated block. A post (/events/<slug>/): one event, its own date."""
    tree = LexborHTMLParser(page.html)
    art = og_image(page.html)
    found = [parsed for block in blocks(tree) if (parsed := parse_block(block, page, s, timezone))]
    out = []
    if page.url.rstrip("/") == s.site_url.rstrip("/"):
        out = [event for event, _ in found]
    elif found:
        # The post's own date: one with an hour, else one after a cue, else the first (document order).
        event, _ = max(found, key=lambda f: (f[0].time_known, f[1], -found.index(f)))
        h1 = tree.css_first("h1")
        title = (clean_str(h1.text()) if h1 else None) or event.title
        slug = page.url.rstrip("/").rsplit("/", 1)[-1]
        body = tree.css_first(".entry-content") or tree.body
        content = body.text(separator=" ") if body else ""
        images = list(dict.fromkeys([*([art] if art else []), *event.images]))
        out = [
            event.model_copy(
                update={
                    "title": title,
                    "source_event_id": f"{event.start_at:%Y%m%d}-{slug}",
                    "source_genres": kinds(title, event.description or "", s) or kinds("", content, s),
                    "images": images,
                }
            )
        ]
    for event in out:
        if not event.images and art:  # a block with no picture of its own
            event.images = [art]
    return out


def undated(page: Page, others: dict[str, str], s: FourNatureSettings) -> list[ScrapedEvent]:
    """The home page's undated offers in ``undated_sections``: each heading (h3) with a button and no date. The link
    is the button's (the sign-up / details page); the place comes from that page when the block names none."""
    tree = LexborHTMLParser(page.html)
    art = og_image(page.html)
    out: list[ScrapedEvent] = []
    section, name, parts = "", "", []

    def flush() -> None:
        # A dated offer is parse_block's (the wasp lecture); one with no button is no offer.
        dated = any(DATE.search(p.text(separator=" ")) for p in parts)
        if name and parts and not dated and (event := offer(name, parts, page, others, s, art)):
            out.append(event)

    # Elementor's widgets in page order: an h2 opens a section, an h3 an offer; the text, button and picture
    # widgets after it are that offer's (two offers can share one container: the course and the trainings).
    for widget in tree.css(".elementor-widget"):
        if h2 := widget.css_first("h2"):
            flush()
            section, name, parts = clean_str(h2.text()) or "", "", []
        elif section not in s.undated_sections:
            continue
        elif h3 := widget.css_first("h3"):
            flush()
            name, parts = clean_str(h3.text()) or "", [widget]  # the heading's widget often holds the text too
        elif name:
            parts.append(widget)
            if "elementor-widget-button" in (widget.attributes.get("class") or ""):
                flush()  # an offer ends at its button: what follows (a dated lecture with no heading) is not its
                name, parts = "", []
    flush()
    return out


def undated_listing(page: Page, others: dict[str, str], s: FourNatureSettings) -> list[ScrapedEvent]:
    """/events/'s coming events with no date (a course whose round is not set, a lecture booked on request): the
    sections between ``listing_future`` and ``listing_past``, each titled by its own page's heading."""
    tree = LexborHTMLParser(page.html)
    pattern = re.compile(s.event_link)
    out: list[ScrapedEvent] = []
    coming = False
    for box in tree.css("section.elementor-top-section, .e-con.e-parent"):
        text = box.text(separator=" ")
        if s.listing_past in text:
            break
        if s.listing_future in text:
            coming = True
            continue
        if not coming or DATE.search(text):
            continue
        hrefs = [urljoin(page.url, a.attributes.get("href") or "") for a in box.css("a[href]")]
        link = next((u for u in hrefs if pattern.match(u)), None)
        if not link:
            continue
        lines = [t for t in (clean_str(x) for x in box.text(separator="\n").split("\n")) if t]
        name = lines[0] if lines else ""  # the listing's own title ("ערב טבע עירוני – סדרת הרצאות על טבע בעיר")
        if name and (event := offer(name, [box], page, others, s, og_image(page.html))):
            out.append(event)
    return out


def undated_catalogue(page: Page, s: FourNatureSettings) -> list[ScrapedEvent]:
    """A catalogue page's courses (``catalogue_paths``): Elementor's widgets in page order, a heading opens a course,
    its button ("לפרטים והזמנה", to the page's order form) closes it; one with no text of its own (the page's
    opening "להרשמה") or no button (the full course, listed from its own page) is none. No place: they come to you."""
    tree = LexborHTMLParser(page.html)
    out: list[ScrapedEvent] = []
    name, parts = "", []
    for widget in tree.css(".elementor-widget"):
        kind = widget.attributes.get("class") or ""
        if head := widget.css_first("h1, h2, h3"):
            name, parts = clean_str(head.text()) or "", []  # the last heading before the text names the course
        elif name and "elementor-widget-button" in kind:
            texts = [p for p in parts if "elementor-widget-text-editor" in (p.attributes.get("class") or "")]
            if texts and (event := offer(name, [*parts, widget], page, {}, s, None)):
                found = list(dict.fromkeys([s.catalogue_kind, *event.source_genres]))
                page_key = f"{event.url}#{event.source_event_id}"  # one form for all: dedup by the course
                out.append(event.model_copy(update={"source_genres": found, "venue_name": None, "address": None,
                                                    "latitude": None, "longitude": None,
                                                    "extra": {**event.extra, "page": page_key}}))  # fmt: skip
            name, parts = "", []
        elif name:
            parts.append(widget)
    return out


def offer(
    name: str, parts: list[LexborNode], page: Page, others: dict[str, str], s: FourNatureSettings, art: str | None
) -> ScrapedEvent | None:
    """One undated offer from its widgets: its button's link (the sign-up / details page), the place they or that
    page name, their picture or that page's."""
    hrefs = [urljoin(page.url, a.attributes.get("href") or "") for p in parts for a in p.css("a[href]")]
    link = next((u for u in hrefs if u.startswith(("https://", "http://"))), None)
    if not link:
        return None
    lines = [t for p in parts for t in (clean_str(x) for x in p.text(separator="\n").split("\n")) if t]
    text = re.sub(r"\s+", " ", " ".join(lines))
    linked_html = others.get(link.split("#")[0].rstrip("/") + "/") or others.get(link) or ""
    linked_text = LexborHTMLParser(linked_html).text(separator=" ") if linked_html else ""
    place = next((p for p in s.places if any(w in text for w in p.match)), None) or next(
        (p for p in s.places if any(w in linked_text for w in p.match)), None
    )
    images = [urljoin(page.url, i.attributes.get("src") or "") for p in parts for i in p.css("img")]
    images = [re.sub(r"-\d+x\d+(?=\.\w+$)", "", u) for u in images if not any(w in u.lower() for w in s.skip_images)]
    if not images and (picture := og_image(linked_html) if linked_html else None):
        images = [picture]
    # The page the button opens has its own sign-up button (rescue-course: "להרשמה" -> its #form): that is the link.
    signup = signup_link(linked_html, link.split("#")[0], s) if linked_html else None
    lines = linked(parts, lines, page.url)
    if signup and signup not in "\n".join(lines):
        lines.append(f"[{s.signup_words[1]}]({signup})")
    key = re.sub(r"\W+", "-", name)[:60].strip("-")
    return ScrapedEvent(
        source=FourNatureScraper.name,
        source_event_id=f"undated-{key}",
        url=signup or link,
        title=name,
        start_at=None,
        time_known=False,
        venue_name=place.venue_name if place else None,
        address=place.address if place else None,
        city=s.city,
        latitude=place.latitude if place else None,
        longitude=place.longitude if place else None,
        description="\n".join(lines),
        source_genres=kinds(name, text, s) or kinds("", linked_text, s),
        images=list(dict.fromkeys(images or ([art] if art else []))),
        organizer="קהילה למען הטבע",
        extra={"purchase_url": signup or link, "page": link},
    )


def signup_link(html: str, base: str, s: FourNatureSettings) -> str | None:
    """A page's own sign-up button ("להרשמה", "הרשמה"): where it leads, absolute (an anchor on the page: its form)."""
    for a in LexborHTMLParser(html).css("a[href]"):
        label = clean_str(a.text()) or ""
        href = urljoin(base, a.attributes.get("href") or "")
        if label in s.signup_words and href.startswith(("https://", "http://")):
            return href
    return None


def pictures(block: LexborNode) -> list[LexborNode]:
    """The block's pictures; with none, those of the nearest container around it that holds no other event."""
    node: LexborNode | None = block
    while node is not None and node.tag != "body":
        if node == block or "e-con" in (node.attributes.get("class") or ""):
            if node != block and sum(1 for _ in DATE.finditer(node.text(separator=" "))) > len(
                list(DATE.finditer(block.text(separator=" ")))
            ):
                return []  # it holds another event's dates
            if found := node.css("img"):
                return found
        node = node.parent
    return []


def linked(parts: list[LexborNode], lines: list[str], base: str) -> list[str]:
    """The text with each link's address: a button's label ("להרשמה", "לפרטים") becomes a Markdown link to where it
    leads (the event page renders those), else the link is added at the end ("בהרשמה מראש בקישור:" then the form)."""
    out = list(lines)
    for a in (a for p in parts for a in p.css("a[href]")):
        href = urljoin(base, a.attributes.get("href") or "")
        label = clean_str(a.text())
        if not href.startswith(("https://", "http://")) or not label or href in "\n".join(out):
            continue
        link = f"[{label}]({href})"
        if label in out:
            out[out.index(label)] = link
        else:
            out.append(link)
    return out


def parse_block(
    block: LexborNode, page: Page, s: FourNatureSettings, timezone: str
) -> tuple[ScrapedEvent, bool] | None:
    """One dated block, and whether a cue word stands just before its date."""
    lines = [t for t in (clean_str(x) for x in block.text(separator="\n").split("\n")) if t]
    text = re.sub(r"\s+", " ", " ".join(lines))
    when = event_date(text, page.written, s)
    if not when:
        return None
    day, m = when
    heading = block.css_first("h1, h2, h3, h4, h5")
    title = clean_str(heading.text()) if heading else None
    if not title:
        title = re.split(r"(?<=[^\d])\.\s", text, maxsplit=1)[0][:150]
    title = re.sub(r"\s*[–-]\s*\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\s*$", "", title).strip()

    tz = zone(timezone)
    hours = HOURS.search(text, m.end(), m.end() + 60)
    if hours:
        start = datetime(day.year, day.month, day.day, int(hours.group(1)), int(hours.group(2)), tzinfo=tz)
        end = start + timedelta(minutes=s.default_duration_minutes)
        if hours.group(3):
            end = datetime(day.year, day.month, day.day, int(hours.group(3)), int(hours.group(4)), tzinfo=tz)
            end = end if end > start else end + timedelta(days=1)
    else:
        start, end = datetime(day.year, day.month, day.day, tzinfo=tz), None

    place = next((p for p in s.places if any(w in text for w in p.match)), None)
    signup = None
    page_link = None
    for a in block.css("a[href]"):
        href = urljoin(page.url, a.attributes.get("href") or "")
        label = clean_str(a.text()) or ""
        if not href.startswith("http"):
            continue
        if "4nature.org.il" in href:
            page_link = page_link or (href if re.match(s.event_link, href.split("#")[0]) else None)
        elif signup is None and any(w in label for w in s.signup_words):
            signup = href
    images = []
    for img in pictures(block):
        src = urljoin(page.url, img.attributes.get("src") or "")
        if src.startswith("http") and not any(w in src.lower() for w in s.skip_images):
            images.append(re.sub(r"-\d+x\d+(?=\.\w+$)", "", src))  # the full picture, not the thumbnail
    free = any(w in text for w in s.free_words)
    age = AGE.search(text)
    # The event's link (our "tickets / sign-up" button, /go): its sign-up form when it has one, else its own page.
    url = signup or (page.url if page.url.rstrip("/") != s.site_url.rstrip("/") else (page_link or page.url))
    key = re.sub(r"\W+", "-", title)[:60].strip("-")
    cue = any(w in text[max(0, m.start() - 25) : m.start()] for w in s.date_cues)
    event = ScrapedEvent(
        source=FourNatureScraper.name,
        source_event_id=f"{start:%Y%m%d}-{key}",
        url=url,
        title=title,
        start_at=start,
        end_at=end,
        time_known=hours is not None,
        venue_name=place.venue_name if place else None,
        address=place.address if place else None,
        city=s.city,
        latitude=place.latitude if place else None,
        longitude=place.longitude if place else None,
        description="\n".join(linked([block], lines, page.url)),
        source_genres=kinds(title, text, s),
        images=list(dict.fromkeys(images)),
        organizer="קהילה למען הטבע",
        min_age=int(age.group(1)) if age else None,
        tickets=[TicketTier(name="השתתפות ללא תשלום", price=0, is_free=True)] if free else [],
        extra={"purchase_url": signup} if signup else {},
    )
    return event, cue
