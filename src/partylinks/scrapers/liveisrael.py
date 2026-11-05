"""LiveIsrael (liveisrael.co): a party guide (club nights, trance and nature parties, festivals), all of Israel.

A WordPress site whose ``party_events`` post type is public in wp-json; Mod_Security answers plain clients
with 406, so it is read with ``http.impersonate: chrome``.
- ``/wp-json/wp/v2/event_tags`` (one request): the site's tags, id -> name: genres (טכנו, טראנס, מיינסטרים),
  kinds (מועדון, פסטיבל, טבע), regions / cities (תל אביב, מרכז, צפון) and ages (18+, 21+). Ages become
  ``min_age``; the rest are kept as source genres.
- ``/wp-json/wp/v2/party_events`` in pages of 100 with the featured image embedded (~5 requests, ~440 rows):
  ``acf`` gives the date (``event_date``, YYYYMMDD), the seller's link (``event_link``: Zygo, Go-Out, a bit.ly),
  kept as ``extra["purchase_url"]`` so dedup merges the event with that seller's listing, and the lineup
  (``lineup_artists``, one name a line). REST has no date filter: past rows are dropped from the listing by
  their ``event_date`` and ``is_current`` (~45 current events); no event page is fetched.
The article's "פרטים חשובים" list states the opening time ("שעת פתיחה: 23:59") and the place ("מיקום:
מועדון שלוותה, התערוכה 3, נמל תל אביב"); a secret spot ("המיקום המדויק נשלח לרוכשים") leaves only the city
of the tags. robots.txt disallows nothing.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_AGE = re.compile(r"^(\d{2})\+$")


class LiveIsraelSettings(SourceSettings):
    site_url: str = "https://liveisrael.co"
    events_api_path: str = "/wp-json/wp/v2/party_events"
    tags_api_path: str = "/wp-json/wp/v2/event_tags"
    per_page: int = 100  # the REST API maximum
    max_pages: int = Field(50, ge=1)
    # Labels of the article's details list.
    # A details label with one of these words holds the opening time ("שעת פתיחת דלתות", "תאריך ושעות").
    time_label_words: list[str] = Field(default_factory=lambda: ["שעה", "שעות", "שעת"])
    age_label_word: str = "גיל"
    # A start before this hour belongs to the night of the listed date ("05.11, דלתות ב-00:00" is Friday 00:00).
    night_until_hour: int = Field(6, ge=0, le=12)
    place_labels: list[str] = Field(default_factory=lambda: ["מיקום", "לוקיישן"])
    # A place with one of these is a secret spot, sent to ticket holders: no address to geocode.
    secret_words: list[str] = Field(default_factory=lambda: ["סודי", "נשלח", "יישלח", "יימסר", "יפורסם"])
    # Place parts that are a street or a region, never a venue name.
    street_words: list[str] = Field(
        default_factory=lambda: ["רחוב", "רח'", "שדרות", "שד'", "דרך", "כיכר", "מרכז הארץ", "שטח פתוח"]
    )
    venue_prefixes: list[str] = Field(default_factory=lambda: ["מועדון "])
    # Tags that are a city: the event's city when its place is secret.
    city_tags: list[str] = Field(default_factory=lambda: ["תל אביב", "חיפה", "ירושלים", "באר שבע", "הרצליה", "אילת"])
    # Events without a genre tag on a party guide are parties.
    base_genre: str = "מסיבה"


class LiveIsraelScraper(BaseScraper[LiveIsraelSettings]):
    name = "liveisrael"
    settings_model = LiveIsraelSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        terms = await self.get_json(s.site_url + s.tags_api_path, params={"per_page": s.per_page, "_fields": "id,name"})
        tags = {t["id"]: html.unescape(str(t["name"])) for t in terms if isinstance(t, dict) and t.get("id")}

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia",
                      "_fields": "id,link,title,content,acf,event_tags,_links,_embedded"}  # fmt: skip
            response = await self.request("GET", s.site_url + s.events_api_path, params=params)
            rows = response.json()
            if not isinstance(rows, list):
                raise ParseError(f"{response.request.url}: expected a list of events")
            return [r for r in rows if isinstance(r, dict)], response.headers.get("X-WP-Total")

        rows = await self.paginate(page, s.per_page, s.max_pages)
        self.log.debug("listed %d events", len(rows))
        for row in rows:
            try:
                event = parse_event(row, tags, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                yield event


def details(content: str | None) -> dict[str, str]:
    """The article's "<strong>label:</strong> value" list items, label -> value."""
    found: dict[str, str] = {}
    for li in LexborHTMLParser(content or "").css("li"):
        label = li.css_first("strong")
        if label is None:
            continue
        key = label.text(strip=True).rstrip(":").strip()
        value = " ".join(li.text().split()).removeprefix(label.text().strip()).strip(" :")
        if key and value:
            found.setdefault(key, value)
    return found


def split_place(place: str | None, s: LiveIsraelSettings) -> tuple[str | None, str | None]:
    """(venue, address) of "מועדון שלוותה, התערוכה 3, נמל תל אביב"; a secret spot gives (None, None)."""
    if not place or any(w in place for w in s.secret_words):
        return None, None
    place = re.sub(r"\s*\([^)]*\)", "", place).strip(" .")
    if not place:
        return None, None
    parts = [p.strip() for p in place.split(",") if p.strip()]
    first = parts[0]
    for prefix in s.venue_prefixes:
        first = first.removeprefix(prefix)
    is_venue = (
        len(parts) >= 2
        and not any(ch.isdigit() for ch in first)
        and not any(first.startswith(w) for w in s.street_words)
    )
    return (first if is_venue else None), ", ".join(parts)


def _image(row: dict[str, Any]) -> list[str]:
    media = (row.get("_embedded") or {}).get("wp:featuredmedia") or []
    url = clean_str(media[0].get("source_url")) if media and isinstance(media[0], dict) else None
    return [url] if url and url.startswith("http") else []


def parse_event(row: dict[str, Any], tags: dict[int, str], s: LiveIsraelSettings, timezone: str) -> ScrapedEvent | None:
    """One REST row -> event; None without an id, title or date."""
    acf = row.get("acf") if isinstance(row.get("acf"), dict) else {}
    raw_title = acf.get("event_title") or (row.get("title") or {}).get("rendered") or ""
    # "06.11 | Rabbit | ראביט - מסיבת שישי": the date prefix repeats the date.
    title = clean_str(re.sub(r"^\s*\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\s*\|\s*", "", html.unescape(str(raw_title))))
    day = clean_str(acf.get("event_date"))
    if not row.get("id") or not title or not day or not re.fullmatch(r"\d{8}", day):
        return None
    content = (row.get("content") or {}).get("rendered") or acf.get("seo_event_description")
    info = details(content)
    clock = next(
        (m for k, v in info.items() if any(w in k for w in s.time_label_words) and (m := _CLOCK.search(v))), None
    )
    start = datetime.strptime(day, "%Y%m%d").replace(tzinfo=zone(timezone))
    if clock:
        start = start.replace(hour=int(clock.group(1)) % 24, minute=int(clock.group(2)))
        if start.hour < s.night_until_hour:
            start += timedelta(days=1)
    venue, address = split_place(next((info[k] for k in s.place_labels if k in info), None), s)
    names = [tags[t] for t in row.get("event_tags") or [] if t in tags]
    ages = [int(m.group(1)) for n in names if (m := _AGE.match(n))]
    ages += [int(m.group(0)) for k, v in info.items() if s.age_label_word in k and (m := re.search(r"\d{2}", v))]
    city = next((n for n in names if n in s.city_tags), None)
    genres = [n for n in names if not _AGE.match(n)] or [s.base_genre]
    link = clean_str(acf.get("event_link"))
    artists = [a for line in str(acf.get("lineup_artists") or "").splitlines() if (a := clean_str(line))]
    return ScrapedEvent(
        source=LiveIsraelScraper.name,
        source_event_id=str(row["id"]),
        url=str(row.get("link") or ""),
        title=title,
        description=html_to_text(content),
        start_at=start,
        time_known=clock is not None,
        venue_name=venue,
        address=address,
        city=city if not address else None,
        min_age=min(ages) if ages else None,
        source_genres=list(dict.fromkeys(g.lower() for g in genres)),
        artists=artists,
        images=_image(row),
        extra={"purchase_url": link} if link and link.startswith("http") else {},
    )
