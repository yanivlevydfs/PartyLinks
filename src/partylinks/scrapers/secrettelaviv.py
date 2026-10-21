"""Secret Tel Aviv (secrettelaviv.com/tickets) - curated city listings (parties, live music, culture...).

WordPress + Events Manager, no JSON-LD and no events in the REST API.
Listing: Events Manager's iCal feed (``/?ical=1``, published for calendar subscriptions) lists
every upcoming event in one request: page URL, title ("Title @ Venue -  - dates"), start / end,
short description, image and categories.
Details: each event page gives the date line ("Sat 24 October 2026 | 11:00 pm - 11:55 pm",
local time; "Tue 24 November 2026 - Sat 28 November 2026 | ..." for a run), the full
description, a "Location:" line (venue name), a "Check out the event:" link to the original
event / seller (kept as ``extra["purchase_url"]``), ``og:image`` and the event's categories
(``event-categories-<slug>`` classes, as ``source_genres``). The site shows no price.
Cloudflare answers some clients with a challenge (``cf-mitigated: challenge``): the run stops as
blocked, but events new to us are first saved as the feed gives them (stored ones keep their details).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings

_DATE = re.compile(r"(\d{1,2}) ([A-Za-z]+) (\d{4})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)", re.IGNORECASE)
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
_CATEGORY_CLASS = "event-categories-"
_ICAL_ESCAPES = re.compile(r"\\([\\;,nN])")
_ICAL_TIME = re.compile(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})\d{2})?")


class SecretTelAvivSelectors(BaseModel):
    title: str = "h1.post-title"
    post: str = "div.hentry"
    body: str = "div.em-event-single"


class SecretTelAvivLabels(BaseModel):
    """Prefixes of the labelled lines at the end of an event's description."""

    location: str = "Location:"
    original_event: str = "Check out the event:"
    disclaimer: str = "* Information on this page"


class SecretTelAvivSettings(SourceSettings):
    site_url: str
    feed_path: str = "/?ical=1"
    event_path_prefix: str = "/tickets/"
    # End time the site fills in when the organiser gave none ("until late"): dropped.
    placeholder_end_time: str = "11:55 pm"
    selectors: SecretTelAvivSelectors = Field(default_factory=SecretTelAvivSelectors)
    labels: SecretTelAvivLabels = Field(default_factory=SecretTelAvivLabels)


class SecretTelAvivScraper(BaseScraper[SecretTelAvivSettings]):
    name = "secrettelaviv"
    settings_model = SecretTelAvivSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return response.headers.get("cf-mitigated") == "challenge"  # Cloudflare bot check

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        feed = parse_feed(await self.get_text(s.site_url + s.feed_path), s, self.timezone)
        self.log.info("listed %d events", len(feed))
        done: set[str] = set()

        async def detail(event: ScrapedEvent) -> ScrapedEvent | None:
            try:
                return await self._fetch_event(str(event.url))
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - fall back to the feed's fields
                self.record_failure(str(event.url), exc)
                return self._feed_only(event)

        try:
            async for event in self.stream_isolated(feed, detail, label=lambda e: str(e.url)):
                done.add(event.source_event_id)
                yield event
        except SourceBlockedError:
            for event in feed:
                if event.source_event_id not in done and (kept := self._feed_only(event)):
                    yield kept
            raise

    def _feed_only(self, event: ScrapedEvent) -> ScrapedEvent | None:
        """A feed event without its page lacks the original-event link: only new events use it,
        so a stored event is not overwritten with less."""
        return None if event.source_event_id in self.known_images else event

    async def _fetch_event(self, url: str) -> ScrapedEvent:
        return parse_event(await self.get_text(url), url, self.settings, self.timezone)


def event_slug(url: str, s: SecretTelAvivSettings) -> str:
    path = urlsplit(url).path
    if not path.startswith(s.event_path_prefix):
        raise ParseError(f"not an event url: {url}")
    slug = path[len(s.event_path_prefix) :].strip("/")
    if not slug or "/" in slug:
        raise ParseError(f"not an event url: {url}")
    return slug


def parse_feed(ics: str, s: SecretTelAvivSettings, timezone: str) -> list[ScrapedEvent]:
    """Events of the iCal feed, one per event page (first wins)."""
    tz = ZoneInfo(timezone)
    events: dict[str, ScrapedEvent] = {}
    for block in re.sub(r"\r?\n[ \t]", "", ics).split("BEGIN:VEVENT")[1:]:
        props: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                props.setdefault(
                    key.split(";")[0],
                    _ICAL_ESCAPES.sub(lambda m: "\n" if m[1] in "nN" else m[1], value),
                )
        url = (props.get("URL") or "").split("?")[0].rstrip("/")
        try:
            slug = event_slug(url, s)
        except ParseError:
            continue
        title = (props.get("SUMMARY") or "").split(" -  - ")[0].strip()
        venue = title.rpartition(" @ ")[2] if " @ " in title else None
        start, end = (
            _ical_time(props.get("DTSTART"), tz),
            _ical_time(props.get("DTEND"), tz),
        )
        all_day = start is not None and len(props.get("DTSTART", "")) == 8
        placeholder = _TIME.search(s.placeholder_end_time)
        if all_day or (
            start
            and end
            and end.date() == start.date()
            and placeholder
            and (end.hour, end.minute) == _clock(placeholder.groups())
        ):
            end = None
        image = props.get("ATTACH")
        events.setdefault(
            slug,
            ScrapedEvent(
                source=SecretTelAvivScraper.name,
                source_event_id=slug,
                url=url,
                title=title,
                description=clean_str(props.get("DESCRIPTION")),
                start_at=start,
                end_at=end if end and start and end > start else None,
                venue_name=clean_str(venue),
                source_genres=[c.strip().lower() for c in (props.get("CATEGORIES") or "").split(",") if c.strip()],
                image_url=image if image and image.startswith("http") else None,
                extra={"purchase_url": None},
            ),
        )
    return list(events.values())


def _ical_time(value: str | None, tz: ZoneInfo) -> datetime | None:
    m = _ICAL_TIME.fullmatch(value or "")
    return datetime(*(int(g or 0) for g in m.groups()), tzinfo=tz) if m else None


def parse_event(page_html: str, url: str, s: SecretTelAvivSettings, timezone: str) -> ScrapedEvent:
    tree = LexborHTMLParser(page_html)
    sel, labels = s.selectors, s.labels
    body = tree.css_first(sel.body)
    if body is None:
        raise ParseError("event body not found")
    blocks = [node for node in body.iter() if node.tag and node.tag[0].isalpha()]  # elements only
    header = blocks[0].text(separator=" ") if blocks and blocks[0].tag == "p" else ""
    start_at, end_at = parse_when(header, s.placeholder_end_time, ZoneInfo(timezone))

    description, venue, original = [], None, None
    for node in blocks[1:]:
        text = clean_str(node.text(separator=" ")) or ""
        if text.startswith(labels.location):
            venue = clean_str(text[len(labels.location) :])
        elif text.startswith(labels.original_event):
            link = node.css_first("a[href]")
            href = link.attributes.get("href") if link else None
            original = href if href and href.startswith("http") else None
        elif text.startswith(labels.disclaimer):
            break
        elif venue is None and original is None and node.tag != "hr":
            description.append(node.html or "")

    # Events Manager's own location line ("<a>Venue</a> - street, town,"), empty on most events.
    em_venue, address, city = _em_location(blocks[0]) if header else (None, None, None)
    post = tree.css_first(sel.post)
    categories = (
        [
            c[len(_CATEGORY_CLASS) :].replace("-", " ")
            for c in (post.attributes.get("class") or "").split()
            if c.startswith(_CATEGORY_CLASS)
        ]
        if post
        else []
    )
    title_node = tree.css_first(sel.title)
    og_image = tree.css_first('meta[property="og:image"]')
    image = og_image.attributes.get("content") if og_image else None
    return ScrapedEvent(
        source=SecretTelAvivScraper.name,
        source_event_id=event_slug(url, s),
        url=url,
        title=(clean_str(title_node.text()) if title_node else None) or "",
        description=html_to_text("".join(description)),
        start_at=start_at,
        end_at=end_at,
        venue_name=em_venue or venue,
        address=address,
        city=city,
        source_genres=categories,
        image_url=image if image and image.startswith("http") else None,
        extra={"purchase_url": original},
    )


def parse_when(header: str, placeholder_end: str, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Start / end from the date line; an end before the start rolls over to the next day."""
    days = [(int(d), _MONTHS.get(m.lower()), int(y)) for d, m, y in _DATE.findall(header)]
    days = [(y, m, d) for d, m, y in days if m]
    if not days:
        return None, None
    times = _TIME.findall(header)
    start_t = _clock(times[0]) if times else (0, 0)
    start = datetime(*days[0], *start_t, tzinfo=tz)
    if len(times) < 2 or (len(days) == 1 and _same(times[1], placeholder_end)):
        return start, None
    end = datetime(*days[-1], *_clock(times[1]), tzinfo=tz)
    return start, end if end > start else end + timedelta(days=1)


def _clock(t: Sequence[str]) -> tuple[int, int]:
    hour, minute, half = int(t[0]) % 12, int(t[1]), t[2].lower()
    return hour + (12 if half == "pm" else 0), minute


def _same(t: Sequence[str], text: str) -> bool:
    m = _TIME.search(text)
    return bool(m) and _clock(t) == _clock(m.groups())


def _em_location(header: LexborNode) -> tuple[str | None, str | None, str | None]:
    """Events Manager's location line ("<a>Venue</a> - street, town, region"); empty on most events."""
    link = header.css_first("a")
    venue = clean_str(link.text()) if link else None
    lines = re.split(r"<br\s*/?>", header.html or "", flags=re.IGNORECASE)
    line = html_to_text(lines[1]) if venue and len(lines) > 1 else None
    parts = [p.strip() for p in line.split(" - ", 1)[1].split(",")] if line and " - " in line else []
    parts = [p for p in parts if p]
    return venue, (parts[0] if parts else None), (parts[1] if len(parts) > 1 else None)
