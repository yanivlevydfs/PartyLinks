"""Tel Aviv-Yafo Jazz Festival (tlvjazzfest26.com, a site per year) - three free days of jazz shows and DJ sets on
the stages of Beit Romano (Derech Yafo 9) and two stages in Park HaMesila next to it.

A static site; three requests a run:

* The home page's schema.org ``Festival`` (JSON-LD): its first day, the venue and its street address.
* ``data.js``: ``window.SCHEDULE``, a JSON list of slots: day ("thu" / "fri" / "sat"), stage, start and end in
  minutes from that day's midnight (1500 = 01:00 the next night), kind ("head" = headliner, "live", "dj") and the
  name in English and in Hebrew.
* ``artists-info.js``: ``window.ARTIST_INFO``, a JavaScript object (unquoted keys, ``//`` comments) of the
  festival's press texts: a Hebrew bio and a photo per artist.

Stored as one event per festival day (its full programme by hour and stage, every performer in the lineup) and
one per headliner show (its bio and photo): 84 one-hour slots in one building would bury every other event of the
night. Entry is free. Past days are dropped.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, og_image, schema_types, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_NAMES = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}
_HEBREW = re.compile(r"[א-ת]")
_KIND = {"dj": "תקלוט", "head": "הופעה מרכזית", "live": "הופעה"}


class TlvJazzFestSettings(SourceSettings):
    site_url: str = "https://tlvjazzfest26.com"  # this year's festival site
    schedule_path: str = "/data.js"
    artists_path: str = "/artists-info.js"
    title: str = "פסטיבל הג'אז תל אביב-יפו"
    source_genres: list[str] = Field(default_factory=lambda: ["ג'אז", "פסטיבל"])
    # Lineup entries that are not a performer: the midnight jam and the after party.
    not_artists: list[str] = Field(default_factory=lambda: ["jam session", "ג׳אם", "after party"])
    free_tier: str = "כניסה חופשית"


class TlvJazzFestScraper(BaseScraper[TlvJazzFestSettings]):
    name = "tlvjazzfest"
    settings_model = TlvJazzFestSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        home = await self.get_text(s.site_url + "/")
        festival = parse_festival(home)
        slots = js_value(await self.get_text(s.site_url + s.schedule_path), "SCHEDULE")
        info = await self.optional("artist texts", self.get_text(s.site_url + s.artists_path))
        artists = js_value(info, "ARTIST_INFO") if info else {}
        if not isinstance(slots, list) or not slots:
            raise ParseError(f"{self.name}: no schedule in {s.schedule_path}")
        events = build_events(festival, slots, artists if isinstance(artists, dict) else {}, s, self.timezone)
        for event in sorted(events, key=lambda e: (e.start_at, e.title)):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def parse_festival(home_html: str) -> dict[str, Any]:
    """The home page's Festival node: first day, venue, street, city, poster."""
    node = next((n for n in jsonld_nodes(home_html) if schema_types(n) & {"festival", "event"}), None)
    if node is None:
        raise ParseError("no Festival JSON-LD on the home page")
    location = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    images = node.get("image") if isinstance(node.get("image"), list) else [node.get("image")]
    return {
        "start": date.fromisoformat(str(node.get("startDate"))[:10]),
        "venue": clean_str(location.get("name")),
        "street": clean_str(address.get("streetAddress")),
        "city": clean_str(address.get("addressLocality")),
        "poster": next((i for i in images if isinstance(i, str) and i.startswith("http")), None) or og_image(home_html),
    }


def js_value(script: str, name: str) -> Any:
    """``window.<name> = <literal>;`` as Python: JSON, or a JavaScript object of the same shape (``//`` comment lines,
    unquoted keys one per line, trailing commas)."""
    m = re.search(rf"window\.{name}\s*=\s*(.*?);?\s*$", script, re.DOTALL)
    if not m:
        raise ParseError(f"no window.{name}")
    text = m[1].strip().rstrip(";")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        text = re.sub(r"(?m)^(\s*)([A-Za-z_][\w]*)\s*:", r'\1"\2":', text)
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        return json.loads(text)


def _clock(day: date, minutes: int, tz: str) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=zone(tz)) + timedelta(minutes=minutes)


def _about(info: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
    about = info.get(str(slot.get("name")))
    return about if isinstance(about, dict) else {}


def _name(slot: dict[str, Any], info: dict[str, Any] | None = None) -> str:
    """The Hebrew name: the press text's, else the schedule's when it is Hebrew ("JOHNNY O'NEAL" is not), else
    the English one."""
    for name in (clean_str(_about(info or {}, slot).get("he")), clean_str(slot.get("he"))):
        if name and _HEBREW.search(name):
            return name
    return clean_str(slot.get("name")) or clean_str(slot.get("he")) or ""


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def build_events(
    festival: dict[str, Any], slots: list[dict[str, Any]], info: dict[str, Any], s: TlvJazzFestSettings, tz: str
) -> list[ScrapedEvent]:
    first: date = festival["start"]
    by_day: dict[date, list[dict[str, Any]]] = {}
    for slot in slots:
        weekday = _WEEKDAYS.get(str(slot.get("day")))
        if weekday is None or not isinstance(slot.get("start"), int) or not _name(slot):
            continue
        by_day.setdefault(first + timedelta(days=(weekday - first.weekday()) % 7), []).append(slot)
    place = {"venue_name": festival["venue"], "address": festival["street"], "city": festival["city"]}
    events: list[ScrapedEvent] = []
    for day, day_slots in sorted(by_day.items()):
        day_slots.sort(key=lambda x: (x["start"], str(x.get("stage"))))
        lines = [
            f"{_hhmm(x['start'])}–{_hhmm(int(x.get('end') or x['start']))} · {x.get('stage')} · {_name(x, info)}"
            + (f" ({_KIND[x['kind']]})" if x.get("kind") in ("dj", "head") else "")
            for x in day_slots
        ]
        names = [_name(x, info) for x in day_slots]
        artists = list(dict.fromkeys(n for n in names if not any(w in n.casefold() for w in s.not_artists)))
        heads = [x for x in day_slots if x.get("kind") == "head"]
        intro = f"בהופעות המרכזיות: {', '.join(_name(x, info) for x in heads)}.\n\n" if heads else ""
        events.append(ScrapedEvent(
            source=TlvJazzFestScraper.name,
            source_event_id=day.isoformat(),
            url=s.site_url + "/",
            title=f"{s.title} · יום {_DAY_NAMES[day.weekday()]} {day:%d.%m}",
            description=f"{intro}התוכנית:\n" + "\n".join(lines),
            start_at=_clock(day, min(x["start"] for x in day_slots), tz),
            end_at=_clock(day, max(int(x.get("end") or x["start"]) for x in day_slots), tz),
            **place,
            artists=artists,
            source_genres=list(s.source_genres),
            images=[festival["poster"]] if festival["poster"] else [],
            tickets=[TicketTier(name=s.free_tier, price=0, is_free=True)],
            currency="ILS", min_price=0, max_price=0,
            organizer=s.title,
        ))  # fmt: skip
        for x in heads:
            about, name = _about(info, x), _name(x, info)
            photo = clean_str(about.get("photo"))
            events.append(ScrapedEvent(
                source=TlvJazzFestScraper.name,
                source_event_id=str(x.get("id") or f"{day}-{x['start']}"),
                url=s.site_url + "/",
                title=f"{name} · {s.title}",
                description=clean_str(about.get("bio")) or None,
                start_at=_clock(day, x["start"], tz),
                end_at=_clock(day, int(x.get("end") or x["start"]), tz) if x.get("end") else None,
                **place,
                artists=[name],
                source_genres=list(s.source_genres),
                images=[urljoin(s.site_url + "/", photo)] if photo else ([festival["poster"]] if festival["poster"] else []),
                tickets=[TicketTier(name=s.free_tier, price=0, is_free=True)],
                currency="ILS", min_price=0, max_price=0,
                organizer=s.title,
            ))  # fmt: skip
    return events
