"""Cinema City (cinema-city.co.il) - the chain's eight multiplexes (Glilot, Rishon LeZion,
Jerusalem, Netanya, Kfar Saba, Hadera, Ashdod, Be'er Sheva).

The site is a Wix front end; its showtimes come from the chain's PRES ticketing host
(tickets.cinema-city.co.il), the same JSON API as the ``pres`` source. Two requests:

* ``GET /api/presentations/`` - every upcoming screening of every branch in one call (~4,000
  rows, ~6 MB): id, local ``dateTime``, ``businessDate`` (a 00:15 show belongs to the evening
  before), feature id/name/rating/duration/category, hall, branch (``venueLocationId`` /
  ``locationName``), languages, ``soldout``. No prices, addresses or coordinates.
* ``GET /api/features/`` - every movie with its synopsis HTML and ``imageData`` (poster URL).
  Optional: without it events still carry everything but synopsis and poster.

One event per (movie, branch, business day) within ``days_ahead`` days; ``start_at`` is that
day's first screening, every time is listed in the description and ``extra.showtimes``. The
movie page is ``www.cinema-city.co.il/movie/<featureId>``; branch addresses (from the site's
/locations JSON-LD) are a settings mapping. robots.txt of www allows everything but
``?lightbox=`` URLs; the tickets host has none (404).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_int, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MIN_AGE = re.compile(r"מגיל\s*(\d{1,2})")  # "הותר מגיל 16"; not "עד גיל 9 בליווי מבוגר"


class Branch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class CinemaCitySettings(SourceSettings):
    api_url: str
    presentations_path: str
    features_path: str
    movie_url: str  # formatted with ``id`` (the feature id)
    days_ahead: int = Field(3, ge=1)
    # venueLocationId -> address / city (the API names the branch only).
    branches: dict[str, Branch] = Field(default_factory=dict)


class CinemaCityScraper(BaseScraper[CinemaCitySettings]):
    name = "cinemacity"
    settings_model = CinemaCitySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_json(s.api_url + s.presentations_path, expect=dict)
        features = await self.optional("features", self.get_json(s.api_url + s.features_path, expect=list))
        today = datetime.now(zone(self.timezone)).date()
        events = parse_presentations(listing, features or [], s, self.timezone, today)
        self.log.info("listed %d movie-days", len(events))
        for event in events:
            yield event


def parse_presentations(
    listing: dict[str, Any], features: list[Any], s: CinemaCitySettings, timezone: str, today: date
) -> list[ScrapedEvent]:
    rows = listing.get("presentations")
    if not isinstance(rows, list):
        raise ParseError("listing without presentations")
    last_day = today + timedelta(days=s.days_ahead - 1)
    groups: dict[tuple[str, str, date], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or row.get("private"):
            continue
        start = parse_iso(row.get("dateTime"), timezone)
        movie, branch = clean_str(row.get("featureId")), clean_str(row.get("venueLocationId"))
        if not start or not movie or not branch:
            continue
        try:
            day = date.fromisoformat(str(row.get("businessDate")))
        except ValueError:
            day = start.date()
        if today <= day <= last_day:
            groups[(movie, branch, day)].append((start, row))
    by_id = {clean_str(f.get("id")): f for f in features if isinstance(f, dict)}
    return [
        parse_movie_day(movie, branch, day, sorted(shows, key=lambda x: x[0]), by_id.get(movie) or {}, s)
        for (movie, branch, day), shows in groups.items()
    ]


def parse_movie_day(
    movie: str, branch: str, day: date, shows: list[tuple[datetime, dict[str, Any]]], feature: dict[str, Any],
    s: CinemaCitySettings,
) -> ScrapedEvent:  # fmt: skip
    first = shows[0][1]
    times = list(dict.fromkeys(start for start, _ in shows))
    synopsis = html_to_text(clean_str(feature.get("synopsis")))
    lines = [synopsis, "הקרנות: " + ", ".join(t.strftime("%H:%M") for t in times)]
    rating = clean_str(first.get("featureRatingName"))
    age = _MIN_AGE.search(rating or "")
    place = s.branches.get(branch) or Branch()
    image = clean_str(feature.get("imageData"))
    extra = {
        "showtimes": [t.isoformat() for t in times],
        "movie_id": movie,
        "branch_id": branch,
        "original_title": clean_str(first.get("featureAdditionalName")),
        "runtime_minutes": to_int(first.get("durationInMinutes")),
        "rating": rating,
        "movie_genres": [g for g in [clean_str(first.get("featureCategoryName"))] if g],
        "language": clean_str(first.get("languageISO")),
        "dubbed": clean_str(first.get("dubbedLanguageISO")),
        "subtitles": clean_str(first.get("subbedLanguageISO")),
        "halls": list(dict.fromkeys(h for _, r in shows if (h := clean_str(r.get("venueName"))))),
        "trailer": clean_str(feature.get("trailer")),
    }
    return ScrapedEvent(
        source=CinemaCityScraper.name,
        source_event_id=f"{movie}:{branch}:{day.isoformat()}",
        url=s.movie_url.format(id=movie),
        title=clean_str(first.get("featureName")) or clean_str(feature.get("name")) or movie,
        description="\n\n".join(line for line in lines if line),
        start_at=times[0],
        venue_name=clean_str(first.get("locationName")),
        address=place.address,
        city=place.city,
        latitude=place.latitude,
        longitude=place.longitude,
        source_genres=["קולנוע"],  # movie genres ("קומדיה") would map to wrong categories
        min_age=int(age.group(1)) if age else None,
        image_url=image,
        is_sold_out=all(r.get("soldout") for _, r in shows),
        extra={k: v for k, v in extra.items() if v not in (None, [])},
    )
