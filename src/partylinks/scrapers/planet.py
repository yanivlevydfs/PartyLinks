"""Planet (planetcinema.co.il, ex Yes Planet) and Rav-Hen (rav-hen.co.il) - cinema screenings.

Both chains belong to Theaters Israel and run Cinema City International's site, whose JSON
"data-api-service" (``<api_path>/v1/quickbook/<tenant_id>/...``) feeds the quick-booking widget.
One request lists the branches with screenings until the last wanted day (name, address, city,
coordinates), then one ``film-events`` request per branch and day gives that day's films (name,
length, poster, page link, attribute ids: rating, genres, format, language) and screenings
(local time, booking link, hall, sold-out flag). Planet: 6 branches x 3 days = 19 requests;
Rav-Hen: 2 x 3 = 7. Screenings group into one event per film, branch and day. No synopsis.
Prices: each screening's booking link is a PRES box office (``tickets5.<site>/api/order/<id>``),
whose ``/api/presentations/<id>/ticketGroups`` gives the price levels (see ``pres``). Read once per
event and screening format (2D, IMAX, 4DX, VIP...: each has its own price), from its first such
screening. robots.txt allows everything but /booking, /club and asset paths.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, parse_iso, to_float, to_int, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.pres import parse_ticket_groups, with_tiers

_RATING = re.compile(r"^(\d+)-plus$")
_FORMAT = re.compile(r"^(\d+d\w*|imax|4dx|screenx|vip(-\w+)?|atmos|dolby.*|d-box)$")
_LANGUAGE = re.compile(r"(^|-)(lang|dubbed|subbed)(-|$)")
_RATED_ALL = "all"


class PlanetSettings(SourceSettings):
    site_url: str
    # Per site: /il/data-api-service (Planet), /rh/data-api-service (Rav-Hen).
    api_path: str
    tenant_id: str
    lang: str = "he_IL"
    # Today plus the next days_ahead - 1 days.
    days_ahead: int = 3
    # On the PRES host of each screening's booking link.
    ticket_groups_path: str = "/api/presentations/{id}/ticketGroups"


class PlanetScraper(BaseScraper[PlanetSettings]):
    name = "planet"
    settings_model = PlanetSettings

    def _url(self, path: str) -> str:
        s = self.settings
        return f"{s.site_url}{s.api_path}/v1/quickbook/{s.tenant_id}/{path}"

    async def _body(self, path: str) -> dict[str, Any]:
        payload = await self.get_json(self._url(path), params={"attr": "", "lang": self.settings.lang}, expect=dict)
        if not isinstance(body := payload.get("body"), dict):
            raise ParseError(f"{path}: response without body")
        return body

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        today = datetime.now(zone(self.timezone)).date()
        days = [today + timedelta(days=i) for i in range(self.settings.days_ahead)]
        branches = (await self._body(f"cinemas/with-event/until/{days[-1]}")).get("cinemas") or []
        self.log.debug("listed %d branches", len(branches))

        async def day_events(item: tuple[dict[str, Any], date]) -> list[ScrapedEvent]:
            branch, day = item
            body = await self._body(f"film-events/in-cinema/{branch.get('id')}/at-date/{day}")
            return [await self._priced(e) for e in parse_film_events(body, branch, self.name, self.timezone)]

        jobs = [(b, d) for b in branches if isinstance(b, dict) for d in days]
        async for events in self.stream_isolated(jobs, day_events, label=lambda j: f"branch {j[0].get('id')} {j[1]}"):
            for event in events:
                yield event

    async def _priced(self, event: ScrapedEvent) -> ScrapedEvent:
        """Prices of the event's first screening of each format."""
        kinds: dict[tuple[str, ...], dict[str, Any]] = {}
        for screening in event.extra["screenings"]:
            kinds.setdefault(tuple(screening["formats"]), screening)
        tiers: list[TicketTier] = []
        for formats, screening in kinds.items():
            host = urlsplit(screening.get("booking_url") or "")
            if not host.netloc or not screening.get("id"):
                continue
            url = f"{host.scheme}://{host.netloc}" + self.settings.ticket_groups_path.format(id=screening["id"])
            payload = await self.optional(f"prices {event.source_event_id}", self.get_json(url, expect=dict))
            label = " ".join(f.upper() for f in formats if f != "2d")
            tiers += [
                t.model_copy(update={"name": f"{t.name} {label}"}) if label else t
                for t in parse_ticket_groups(payload or {})
            ]
        return with_tiers(event, tiers) if tiers else event


class RavHenScraper(PlanetScraper):
    """Rav-Hen (קולנוע חן): same platform, its own tenant and API path (see settings)."""

    name = "ravhen"


def split_attributes(ids: list[str]) -> tuple[int | None, list[str], list[str], list[str]]:
    """Attribute ids -> (min age, genres, formats, language flags)."""
    min_age, genres, formats, languages = None, [], [], []
    for attr in ids:
        if rated := _RATING.match(attr):
            min_age = int(rated.group(1))
        elif _FORMAT.match(attr):
            formats.append(attr)
        elif _LANGUAGE.search(attr):
            languages.append(attr)
        elif attr != _RATED_ALL:
            genres.append(attr)
    return min_age, genres, formats, languages


def parse_film_events(body: dict[str, Any], branch: dict[str, Any], source: str, timezone: str) -> list[ScrapedEvent]:
    """One branch's day of ``film-events`` -> one event per film with that day's screenings."""
    films = {f.get("id"): f for f in body.get("films") or [] if isinstance(f, dict)}
    screenings: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for show in body.get("events") or []:
        if isinstance(show, dict) and (start := parse_iso(show.get("eventDateTime"), timezone)):
            screenings[(str(show.get("filmId")), str(show.get("businessDay") or start.date()))].append((start, show))
    events = []
    for (film_id, day), shows in screenings.items():
        if (film := films.get(film_id)) and (
            event := parse_film_day(film, day, sorted(shows, key=lambda s: s[0]), branch, source)
        ):
            events.append(event)
    return events


def parse_film_day(
    film: dict[str, Any], day: str, shows: list[tuple[datetime, dict[str, Any]]], branch: dict[str, Any], source: str
) -> ScrapedEvent | None:
    title, link, branch_id = clean_str(film.get("name")), clean_str(film.get("link")), clean_str(branch.get("id"))
    if not title or not link or not branch_id:
        return None
    attrs = list(dict.fromkeys(a for _, show in shows for a in show.get("attributeIds") or []))
    min_age, _, formats, languages = split_attributes(attrs)
    _, genres, _, _ = split_attributes(film.get("attributeIds") or [])
    info = branch.get("addressInfo") if isinstance(branch.get("addressInfo"), dict) else {}
    return ScrapedEvent(
        source=source,
        source_event_id=f"{film.get('id')}:{branch_id}:{day}",
        url=link,
        title=title,
        description="הקרנות: " + ", ".join(start.strftime("%H:%M") for start, _ in shows),
        start_at=shows[0][0],
        venue_name=clean_str(branch.get("displayName")),
        address=clean_str(info.get("address1")),  # address4 can hold an internal GUID
        city=clean_str(info.get("city")),
        latitude=to_float(branch.get("latitude")),
        longitude=to_float(branch.get("longitude")),
        source_genres=["קולנוע"],
        min_age=min_age,
        image_url=clean_str(film.get("posterLink")),
        is_sold_out=all(show.get("soldOut") for _, show in shows),
        extra={
            "movie_id": film.get("id"),
            "branch_id": branch_id,
            "showtimes": [start.isoformat() for start, _ in shows],
            "screenings": [
                {
                    "start": start.isoformat(),
                    "id": show.get("id"),
                    "formats": split_attributes(show.get("attributeIds") or [])[2],
                    "booking_url": show.get("bookingLink"),
                    "hall": show.get("auditorium"),
                    "sold_out": bool(show.get("soldOut")),
                }
                for start, show in shows
            ],
            "runtime_minutes": to_int(film.get("length")),
            "movie_genres": genres,
            "formats": formats,
            "languages": languages,
            "release_year": film.get("releaseYear"),
            "trailer_url": film.get("videoLink"),
        },
    )
