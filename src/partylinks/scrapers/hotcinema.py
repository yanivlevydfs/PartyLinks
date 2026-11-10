"""HOT Cinema (hotcinema.co.il) - cinema chain: Modiin, Kiryon, Haifa, Kfar Saba, Petah Tikva, Rehovot...

The site is server-rendered (ASP.NET + Vue widgets) with small JSON endpoints behind its order
forms. ``1 + days_ahead`` listing requests per run (prices below):
* the home page: every movie now showing as a poster box with its page link, poster image and
  synopsis (``div.movie-box``), read once for artwork and description;
* ``/tickets/TheaterEvents?movieid=&theatreid=&date=DD/MM/YYYY`` once per day: with empty movie
  and branch it lists every movie screening that day across all branches - ``MovieName``,
  ``MovieId`` and ``Dates[]`` (EventId, TheaterId, local ``Date``, dub/subtitle language, 3D/VIP).
  A day's list also holds its after-midnight shows (00:00 of the next date); they stay on the
  day they were listed under, as on the site.
The feed has no branch names or addresses, so those come from the ``branches`` setting (taken
from the ``/theater/<id>`` pages). No age ratings. Prices come from the BiggerPicture shop the
order button opens: ``/order?theaterId=&eventId=`` redirects to ``<shop>/site/<siteId>?code=...``,
read once per branch for its site id, then one login per site and one request per event and
screening kind (3D / VIP / Atmos), from its first such screening (see ``biggerpicture``).
robots.txt is absent (404): nothing is disallowed.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_int, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.biggerpicture import BiggerPicture
from partylinks.scrapers.pres import with_tiers

_SITE = re.compile(r"/site/(\d+)")


class HotCinemaBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class HotCinemaSettings(SourceSettings):
    site_url: str
    events_path: str
    # Days listed, starting today (3 = today + 2).
    days_ahead: int = 3
    # TheaterId -> branch; an unknown id is still listed, under the chain name only.
    branches: dict[int, HotCinemaBranch] = Field(default_factory=dict)
    chain_name: str = "הוט סינמה"
    # The order button: redirects to the BiggerPicture shop page of the branch's site.
    order_path: str = "/order?theaterId={theater}&eventId={event}"
    tickets_api: str = "https://pub-api-use1.biggerpicture.ai/ecomAPI/public/api"
    sale_channel: str = "WEB"
    tickets_language: str = "he_IL"


class Movie(BaseModel):
    url: str
    image: str | None = None
    synopsis: str | None = None


class HotCinemaScraper(BaseScraper[HotCinemaSettings]):
    name = "hotcinema"
    settings_model = HotCinemaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        home = await self.optional("home page", self.get_text(s.site_url + "/"))
        movies = parse_movies(home, s.site_url) if home else {}
        today = datetime.now(zone(self.timezone)).date()
        days = [today + timedelta(days=i) for i in range(s.days_ahead)]

        async def day_events(day: date) -> list[ScrapedEvent]:
            params = {"movieid": "", "theatreid": "", "date": day.strftime("%d/%m/%Y")}
            payload = await self.get_json(s.site_url + s.events_path, params=params, expect=list)
            return parse_day(day, payload, movies, s, self.timezone)

        listed = self.stream_isolated(days, day_events, lambda d: f"day {d.isoformat()}")
        events = [event async for day_list in listed for event in day_list]
        shop = BiggerPicture(self, s.tickets_api, s.sale_channel, s.tickets_language)
        sites: dict[int, asyncio.Task[int]] = {}

        async def site_id(branch_id: int, event_id: str) -> int:
            url = s.site_url + s.order_path.format(theater=branch_id, event=event_id)
            if not (found := _SITE.search((await self.request("GET", url)).url.path)):
                raise ParseError(f"branch {branch_id}: order page without a shop site")
            return int(found.group(1))

        async def shop_tiers(branch_id: int, event_id: str) -> list[TicketTier]:
            return await shop.tiers(await asyncio.shield(sites[branch_id]), event_id)

        async def priced(event: ScrapedEvent) -> ScrapedEvent:
            kinds: dict[tuple[str, ...], str] = {}
            for sc in event.extra["screenings"]:
                kinds.setdefault(tuple(sc["formats"]), sc["event_id"])
            branch_id = event.extra["branch_id"]
            if branch_id not in sites:  # one lookup per branch, shared by the concurrent events
                sites[branch_id] = asyncio.ensure_future(site_id(branch_id, next(iter(kinds.values()))))
            tiers = []
            for event_id in kinds.values():
                call = shop_tiers(branch_id, event_id)
                tiers += await self.optional(f"prices {event.source_event_id}", call) or []
            return with_tiers(event, tiers) if tiers else event

        async for event in self.stream_isolated(events, priced, lambda e: e.source_event_id):
            yield event


def parse_movies(page_html: str, site_url: str) -> dict[int, Movie]:
    """Movie id -> page link, full-size poster and synopsis from the home page poster boxes."""
    movies: dict[int, Movie] = {}
    for box in LexborHTMLParser(page_html).css("div.movie-box"):
        link = box.css_first("a[href^='/movie/']")
        href = link.attributes.get("href") if link else None
        movie_id = to_int(href.split("/")[2]) if href else None
        if not href or not movie_id or movie_id in movies:
            continue
        img = box.css_first("img")
        src = img.attributes.get("src") if img else None
        synopsis = box.css_first("div.desc-d")
        movies[movie_id] = Movie(
            url=urljoin(site_url, href.split("#")[0]),
            image=urljoin(site_url, urlsplit(src).path) if src else None,  # drop the ?w=&h= crop
            synopsis=html_to_text(synopsis.html) if synopsis else None,
        )
    return movies


def parse_day(
    day: date, payload: list[Any], movies: dict[int, Movie], s: HotCinemaSettings, timezone: str
) -> list[ScrapedEvent]:
    """One event per (movie, branch) screening on ``day``."""
    events = []
    for movie in payload:
        if not isinstance(movie, dict):
            raise ParseError("TheaterEvents item is not an object")
        movie_id, title = to_int(movie.get("MovieId")), clean_str(movie.get("MovieName"))
        if not movie_id or not title:
            continue
        by_branch: dict[int, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        for screening in movie.get("Dates") or []:
            start = parse_iso(screening.get("Date"), timezone) if isinstance(screening, dict) else None
            branch_id = to_int(screening.get("TheaterId")) if start else None
            if start and branch_id:
                by_branch[branch_id].append((start, screening))
        for branch_id, screenings in by_branch.items():
            events.append(_event(day, movie_id, title, branch_id, sorted(screenings, key=lambda x: x[0]), movies, s))
    return events


def _event(
    day: date,
    movie_id: int,
    title: str,
    branch_id: int,
    screenings: list[tuple[datetime, dict[str, Any]]],
    movies: dict[int, Movie],
    s: HotCinemaSettings,
) -> ScrapedEvent:
    info = movies.get(movie_id)
    branch = s.branches.get(branch_id)
    times = ", ".join(start.strftime("%H:%M") for start, _ in screenings)
    rows = [x for _, x in screenings]
    languages = {
        f"{k}:{v}" for x in rows for k in ("DubbedLanguage", "SubtitledLanguage") if (v := clean_str(x.get(k)))
    }
    formats = {f for x in rows for f in _formats(x)}
    return ScrapedEvent(
        source=HotCinemaScraper.name,
        source_event_id=f"{movie_id}:{branch_id}:{day.isoformat()}",
        url=info.url if info else f"{s.site_url}/movie/{movie_id}",
        title=title,
        description="\n\n".join(p for p in (info.synopsis if info else None, f"הקרנות: {times}") if p),
        start_at=screenings[0][0],
        # Current until its last screening starts, not 3 h after the first one (retention.is_current).
        end_at=screenings[-1][0] if len(screenings) > 1 else None,
        venue_name=f"{s.chain_name} {branch.name}" if branch else s.chain_name,
        address=branch.address if branch else None,
        city=branch.city if branch else None,
        latitude=branch.latitude if branch else None,
        longitude=branch.longitude if branch else None,
        # The feed mixes in stand-up nights: those are left to the text genres, not cinema.
        source_genres=[] if "סטנדאפ" in title else ["קולנוע"],
        image_url=info.image if info else None,
        extra={
            "showtimes": [start.isoformat() for start, _ in screenings],
            "event_ids": [clean_str(x.get("EventId")) for x in rows],
            "movie_id": movie_id,
            "branch_id": branch_id,
            "languages": sorted(languages),
            "formats": sorted(formats),
            "screenings": [
                {"start": start.isoformat(), "event_id": clean_str(x.get("EventId")), "formats": _formats(x)}
                for start, x in screenings
            ],
        },
    )


def _formats(screening: dict[str, Any]) -> list[str]:
    return [f for f, key in (("3D", "Is3D"), ("VIP", "IsVIP"), ("Atmos", "IsAtmos")) if screening.get(key)]
