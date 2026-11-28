"""Under The Radar (undertheradar.events, "מתחת לרדאר") - a psytrance party line: daytime-into-night
parties in special locations (4.12.2026: Sharabiya, Old Jaffa, 15:30-00:30), its own one-page site.

The site lists its coming parties in its calendar feed (``/api/event/calendar``, iCalendar: start and
end with the time zone, location, description), and the home page carries each one's card
(``data-event-card="<place>-<YYYY-MM-DD>"``: the poster, the details line, the description). When the
sale opens (the card's ``data-sale-opens``), ``/api/event/launch`` gives the lineup (artist, set hours,
bio), the venue and the ticket link (an EASYTIX page). Before that the lineup and the link are not out
yet: the party is read without them. Not read: ``/past-events`` (an archive), the ride board, the
birthday / business forms.

Cloudflare challenges requests from abroad; from Israel's side of the network the site answers plainly.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD = re.compile(r'<section[^>]*data-event-card="([^"]+)"[^>]*>(.*?)</section>', re.DOTALL)
_CARD_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})$")
_POSTER = re.compile(r'<img[^>]*class="[^"]*poster[^"]*"[^>]*src="([^"]+)"')
_DESCRIPTION = re.compile(r'<p class="description">(.*?)</p>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?)?(Z)?$")


class UtrVenue(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class UndertheradarSettings(SourceSettings):
    site_url: str = "https://undertheradar.events"
    calendar_path: str = "/api/event/calendar"
    launch_path: str = "/api/event/launch"
    # The calendar's LOCATION (lowercase, a part of it) -> our venue: the site names the place only.
    venues: dict[str, UtrVenue] = Field(default_factory=dict)


class UndertheradarScraper(BaseScraper[UndertheradarSettings]):
    name = "undertheradar"
    settings_model = UndertheradarSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        calendar = await self.get_text(s.site_url + s.calendar_path)
        home = await self.optional("home page", self.get_text(s.site_url + "/")) or ""
        launch = await self.optional("launch", self.get_json(s.site_url + s.launch_path))
        retention = RetentionSettings()
        for event in parse(calendar, home, launch if isinstance(launch, dict) else None, s, self.timezone):
            if is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def vevents(ics: str) -> list[dict[str, tuple[dict[str, str], str]]]:
    """The VEVENTs of an iCalendar text: name -> (parameters, value), lines unfolded."""
    lines = re.sub(r"\r?\n[ \t]", "", ics).splitlines()
    out: list[dict[str, tuple[dict[str, str], str]]] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            out.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            current[name.upper()] = (dict(p.split("=", 1) for p in params if "=" in p), _unescape(value))
    return out


def _when(field: tuple[dict[str, str], str] | None, timezone: str) -> datetime | None:
    if not field:
        return None
    params, value = field
    m = _STAMP.match(value.strip())
    if not m or not m.group(4):
        return None
    year, month, day, hour, minute, second, utc = m.groups()
    tz = zone("UTC" if utc else params.get("TZID") or timezone)
    at = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0), tzinfo=tz)
    return at.astimezone(zone(timezone))


def _plain(fragment: str) -> str:
    return " ".join(html_lib.unescape(_TAG.sub(" ", fragment)).split())


def cards(home_html: str, site_url: str) -> dict[str, dict[str, str]]:
    """The home page's party cards by day (YYYY-MM-DD): id, poster URL, description."""
    out: dict[str, dict[str, str]] = {}
    for card_id, inner in _CARD.findall(home_html):
        day = _CARD_DAY.search(card_id)
        if not day:
            continue
        card = {"id": card_id}
        if poster := _POSTER.search(inner):
            src = html_lib.unescape(poster.group(1))
            card["poster"] = src if src.startswith("http") else site_url + "/" + src.lstrip("/")
        if description := _DESCRIPTION.search(inner):
            card["description"] = _plain(description.group(1))
        out["-".join(day.groups())] = card
    return out


def _lineup(launch: dict[str, Any]) -> tuple[list[str], str | None]:
    """The artists, and the lineup as text (the set hours, each artist's bio)."""
    artists, rows = [], []
    for item in launch.get("lineup") or []:
        if not isinstance(item, dict) or not item.get("artist"):
            continue
        artist = str(item["artist"]).strip()
        artists.append(artist)
        head = " ".join(p for p in (artist, str(item.get("hours") or "").strip()) if p)
        rows.append("\n".join(p for p in (head, str(item.get("bio") or "").strip()) if p))
    return artists, ("ליינאפ:\n\n" + "\n\n".join(rows)) if rows else None


def parse(
    ics: str, home_html: str, launch: dict[str, Any] | None, s: UndertheradarSettings, timezone: str
) -> list[ScrapedEvent]:
    """One event per calendar entry; the home page's card and, for the party on sale, the launch data."""
    by_day = cards(home_html, s.site_url)
    events = []
    for entry in vevents(ics):
        start = _when(entry.get("DTSTART"), timezone)
        if start is None:
            continue
        end = _when(entry.get("DTEND"), timezone)
        location = (entry.get("LOCATION") or ({}, ""))[1].strip()
        venue = next((v for key, v in s.venues.items() if key in location.lower()), None)
        card = by_day.get(start.date().isoformat(), {})
        # The launch data is the coming party's: the one whose card the home page shows with the sale.
        mine = launch if launch and card and (not launch.get("venue") or launch["venue"] == location) else None
        artists, lineup = _lineup(mine) if mine else ([], None)
        description = card.get("description") or (entry.get("DESCRIPTION") or ({}, ""))[1].strip() or None
        summary = (entry.get("SUMMARY") or ({}, ""))[1].strip()
        uid = (entry.get("UID") or ({}, ""))[1].strip() or start.date().isoformat()
        ticket_url = mine.get("ticketUrl") if mine else None
        poster = card.get("poster")
        if mine and isinstance(mine.get("poster"), dict) and mine["poster"].get("src"):
            src = str(mine["poster"]["src"])
            poster = src if src.startswith("http") else s.site_url + "/" + src.lstrip("/")
        events.append(
            ScrapedEvent(
                source="undertheradar",
                source_event_id=uid.split("@")[0],
                url=f"{s.site_url}/#{card.get('id') or start.date().isoformat()}",
                title=summary or "מתחת לרדאר",
                description="\n\n".join(p for p in (description, lineup) if p) or None,
                start_at=start,
                end_at=end if end and end > start else None,
                time_known=True,
                venue_name=venue.name if venue else location or None,
                address=venue.address if venue else None,
                city=venue.city if venue else None,
                latitude=venue.latitude if venue else None,
                longitude=venue.longitude if venue else None,
                source_genres=["psytrance"],  # a psytrance line (the WhatsApp group "של הטראנס")
                artists=artists,
                organizer="מתחת לרדאר",
                images=[poster] if poster else [],
                extra={"purchase_url": ticket_url or s.site_url},
            )
        )
    return events
