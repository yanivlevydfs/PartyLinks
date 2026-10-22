"""Tel Aviv Cinematheque (cinema.co.il, "סינמטק תל אביב") - its PRES box office.

The WordPress site sells through PRES (cintlv.pres.global, redirecting to cintlv.presglobal.store),
the same JSON API as the ``pres`` and ``cinemacity`` sources:

* ``GET /api/presentations/`` - every screening in one call (~450 rows, ~0.6 MB, old VOD rows
  included): id, local ``dateTime``, ``businessDate``, feature id/name/category/duration, hall
  (``venueName``: "אולם 1".."אולם 6", "ספריה"), ``languageISO``, ``soldout``.
* ``GET /api/features/`` - every feature's synopsis HTML and ``imageData`` (poster; ~15% have
  none). Optional: without it events keep everything but synopsis and poster.
* ``GET /api/presentations/<id>/ticketGroups`` - prices, read once per feature from its first
  screening in the window (~80 requests a week) and applied to all its days. Optional.

One event per (feature, business day) within ``days_ahead`` days; ``start_at`` is that day's
first screening, every time is listed in the description and ``extra.showtimes``. The site's
event pages are WordPress slugs the API does not give, so ``url`` is the PRES order page of the
first screening. Film + talk screenings ("+ הרצאה") stay films; lecture-course meetings
("... - מפגש 7", "2026-2027" course categories) are tagged ``הרצאה`` instead of ``קולנוע``.
robots.txt of www allows everything but /wp-admin; the PRES host has none.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_int, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.pres import parse_ticket_groups

# A lecture course: a numbered meeting, or a season category ("... 2026-2027", truncated "2026-20").
_COURSE_TITLE = re.compile(r"מפגש\s*\d+")
_COURSE_CATEGORY = re.compile(r"\b20\d\d-20")


class TelAvivCinemathequeSettings(SourceSettings):
    api_url: str
    presentations_path: str
    features_path: str
    ticket_groups_path: str  # formatted with ``id`` (a presentation id)
    order_url: str  # formatted with ``id``
    days_ahead: int = Field(7, ge=1)
    branch_id: str = "tlv"
    venue_name: str = "סינמטק תל אביב"
    address: str | None = "הארבעה 5"
    city: str | None = "תל אביב"


class TelAvivCinemathequeScraper(BaseScraper[TelAvivCinemathequeSettings]):
    name = "cinematheque_tlv"
    settings_model = TelAvivCinemathequeSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_json(s.api_url + s.presentations_path, expect=dict)
        features = await self.optional("features", self.get_json(s.api_url + s.features_path, expect=list))
        today = datetime.now(zone(self.timezone)).date()
        groups = group_screenings(listing, self.timezone, today, s.days_ahead)
        self.log.info("listed %d movie-days", len(groups))

        # Prices from each feature's first screening in the window.
        first: dict[str, Any] = {}
        for (movie, _), shows in sorted(groups.items(), key=lambda g: g[0][1]):
            first.setdefault(movie, shows[0][1].get("id"))

        async def prices(movie: str) -> tuple[str, list[TicketTier]]:
            url = s.api_url + s.ticket_groups_path.format(id=first[movie])
            return movie, parse_ticket_groups(await self.get_json(url, expect=dict))

        tiers = dict([x async for x in self.stream_isolated(first, prices, lambda m: f"prices of feature {m}")])
        by_id = {clean_str(f.get("id")): f for f in features or [] if isinstance(f, dict)}
        for (movie, day), shows in groups.items():
            yield build_event(movie, day, shows, by_id.get(movie) or {}, tiers.get(movie, []), s, self.name)


class JerusalemCinemathequeScraper(TelAvivCinemathequeScraper):
    """Jerusalem Cinematheque (jer-cin.org.il): its box office is PRES too (jer-cin.pres.global).

    Its own site asks for a 180-second crawl delay, so it is not crawled; venue in settings.
    """

    name = "cinematheque_jlm"


def group_screenings(
    listing: dict[str, Any], timezone: str, today: date, days_ahead: int
) -> dict[tuple[str, date], list[tuple[datetime, dict[str, Any]]]]:
    """Public screenings per (feature id, business day) within the window, soonest first."""
    rows = listing.get("presentations")
    if not isinstance(rows, list):
        raise ParseError("listing without presentations")
    last_day = today + timedelta(days=days_ahead - 1)
    groups: dict[tuple[str, date], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or row.get("private"):
            continue
        start, movie = parse_iso(row.get("dateTime"), timezone), clean_str(row.get("featureId"))
        if not start or not movie:
            continue
        try:
            day = date.fromisoformat(str(row.get("businessDate")))  # a 00:15 show belongs to the evening before
        except ValueError:
            day = start.date()
        if today <= day <= last_day:
            groups[(movie, day)].append((start, row))
    for shows in groups.values():
        shows.sort(key=lambda x: x[0])
    return dict(groups)


def build_event(
    movie: str, day: date, shows: list[tuple[datetime, dict[str, Any]]], feature: dict[str, Any],
    tiers: list[TicketTier], s: TelAvivCinemathequeSettings, source: str = "cinematheque_tlv",
) -> ScrapedEvent:  # fmt: skip
    first = shows[0][1]
    times = list(dict.fromkeys(start for start, _ in shows))
    title = clean_str(first.get("featureName")) or clean_str(feature.get("name")) or movie
    category = clean_str(first.get("featureCategoryName")) or clean_str(feature.get("categoryName"))
    course = bool(_COURSE_TITLE.search(title) or _COURSE_CATEGORY.search(category or ""))
    lines = [
        html_to_text(clean_str(feature.get("synopsis"))),
        "הקרנות: " + ", ".join(t.strftime("%H:%M") for t in times),
    ]
    priced = [t.price for t in tiers if t.price is not None]
    extra = {
        "showtimes": [t.isoformat() for t in times],
        "movie_id": movie,
        "branch_id": s.branch_id,
        "purchase_url": s.order_url.format(id=first.get("id")),
        "screening_ids": [r.get("id") for _, r in shows],
        "original_title": clean_str(first.get("featureAdditionalName")),
        "runtime_minutes": to_int(first.get("durationInMinutes")),
        "language": clean_str(first.get("languageISO")) or clean_str(feature.get("originalLanguageISO")),
        "series": [c.strip() for c in (category or "").split(",") if c.strip() and c.strip() != "כללי"],
        "halls": list(dict.fromkeys(h for _, r in shows if (h := clean_str(r.get("venueName"))))),
        "trailer": clean_str(feature.get("trailer")),
    }
    return ScrapedEvent(
        source=source,
        source_event_id=f"{movie}:{s.branch_id}:{day.isoformat()}",
        url=extra["purchase_url"],
        title=title,
        description="\n\n".join(line for line in lines if line),
        start_at=times[0],
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        # Series names ("פסטיבל גאה ריכוז") would map to wrong categories: kept in extra only.
        source_genres=["הרצאה"] if course else ["קולנוע"],
        image_url=clean_str(feature.get("imageData")),
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        tickets=tiers,
        is_sold_out=all(r.get("soldout") for _, r in shows),
        extra={k: v for k, v in extra.items() if v not in (None, [])},
    )
