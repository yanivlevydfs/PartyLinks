"""Movieland (movieland.co.il) - cinema screenings of the chain's six branches.

The site's own JSON ``/api/Events`` (the quick-order widget's feed) answers one request with
every movie on sale and all its screenings in every branch: name, poster file, genres, runtime,
age rating, and per screening the local datetime, branch id and name, language group
(subtitles / Hebrew or Russian dub), VIP flag and booking link. Screenings group into one event
per movie, branch and business day (a screening before 05:00 belongs to the previous day).
No synopsis (only on the ~100 KB movie pages, not fetched) and no prices. The feed carries no
branch addresses, so they live in settings. robots.txt allows everything. One request per run.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

_AGE = re.compile(r"^(\d{1,2})\s*\+")
# A screening listed before this hour runs after midnight of its business day.
_NIGHT_HOUR = 5
# Genre of non-film items (football / show broadcasts): no "קולנוע" for those.
_NOT_FILM = "אירוע"


class MovielandBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue_name: str
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class MovielandSettings(SourceSettings):
    site_url: str
    events_path: str = "/api/Events"
    images_path: str = "/images/"
    movie_path: str = "/movie/{id}"
    # Today plus the next days_ahead - 1 days.
    days_ahead: int = 3
    # By the feed's TheaterId; an unlisted branch is still read, named after the feed.
    branches: dict[str, MovielandBranch] = Field(default_factory=dict)


class MovielandScraper(BaseScraper[MovielandSettings]):
    name = "movieland"
    settings_model = MovielandSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        movies = await self.get_json(s.site_url + s.events_path, params={"isHideVODRent": "true"}, expect=list)
        today = datetime.now(ZoneInfo(self.timezone)).date()
        days = {today + timedelta(days=i) for i in range(s.days_ahead)}
        self.log.info("listed %d movies", len(movies))
        for event in parse_events(movies, s, days, self.timezone):
            yield event


def business_day(start: datetime) -> date:
    return (start - timedelta(days=1)).date() if start.hour < _NIGHT_HOUR else start.date()


def parse_events(movies: list[Any], settings: MovielandSettings, days: set[date], timezone: str) -> list[ScrapedEvent]:
    """The ``/api/Events`` list -> one event per movie, branch and business day in ``days``."""
    tz = ZoneInfo(timezone)
    events = []
    for movie in movies:
        if not isinstance(movie, dict) or not (title := clean_str(movie.get("Name"))):
            continue
        groups: dict[tuple[str, date], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        for show in movie.get("Dates") or []:
            try:
                start = datetime.fromisoformat(show["Date"]).replace(tzinfo=tz)
            except (KeyError, TypeError, ValueError):
                continue
            if (day := business_day(start)) in days:
                groups[(str(show.get("TheaterId")), day)].append((start, show))
        for (branch_id, day), shows in groups.items():
            shows.sort(key=lambda x: x[0])
            events.append(build_event(movie, title, branch_id, day, shows, settings))
    return events


def build_event(
    movie: dict[str, Any],
    title: str,
    branch_id: str,
    day: date,
    shows: list[tuple[datetime, dict[str, Any]]],
    settings: MovielandSettings,
) -> ScrapedEvent:
    movie_id = movie.get("MovieId")
    branch = settings.branches.get(branch_id) or MovielandBranch(
        venue_name=f"מובילנד {clean_str(shows[0][1].get('TheaterName')) or branch_id}"
    )
    genres = [g for g in (clean_str(x) for x in (movie.get("Genres") or "").split(",")) if g]
    rating = clean_str((movie.get("MovieRating") or {}).get("Name"))
    age = _AGE.match(rating) if rating else None
    pic = clean_str(movie.get("Pic"))
    return ScrapedEvent(
        source=MovielandScraper.name,
        source_event_id=f"{movie_id}:{branch_id}:{day.isoformat()}",
        url=settings.site_url + settings.movie_path.format(id=movie_id),
        title=title,
        description="הקרנות: " + ", ".join(start.strftime("%H:%M") for start, _ in shows),
        start_at=shows[0][0],
        venue_name=branch.venue_name,
        address=branch.address,
        city=branch.city,
        latitude=branch.latitude,
        longitude=branch.longitude,
        source_genres=[] if _NOT_FILM in genres else ["קולנוע"],
        min_age=int(age.group(1)) if age else None,
        image_url=settings.site_url + settings.images_path + quote(pic) if pic else None,
        extra={
            "showtimes": [start.isoformat() for start, _ in shows],
            "movie_id": movie_id,
            "branch_id": branch_id,
            "screenings": [
                {
                    "start": start.isoformat(),
                    "event_id": show.get("EventId"),
                    "booking_url": show.get("BookingNativeUrl"),
                    "language": clean_str(show.get("SiteGroup")),
                    "vip": bool(show.get("IsVip")),
                }
                for start, show in shows
            ],
            "movie_genres": genres,
            "runtime_minutes": to_int(movie.get("LengthInMinutes")),
            "rating": rating,
        },
    )
