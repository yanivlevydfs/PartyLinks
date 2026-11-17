"""Skiddle (skiddle.com) - a UK ticketing site for club nights, gigs and festivals.

Only the UK cities Israelis fly to most are read (config ``cities``: London, Manchester), and only
club nights: a city's clubs page (``/clubs/<City>/``, Next.js) embeds its 24 trending upcoming
club nights in ``__NEXT_DATA__`` (``initialResults.events``), so a run is one request per city (2).
robots.txt allows these pages (it disallows query strings, past events and booking paths).

The site sits behind AWS WAF, which answers a JS challenge (202, ``x-amzn-waf-action``) from the
third quick page on (Nov 2026, Railway's IP and ours alike). Two pages a run stay under it, and
the event pages (which carry genres) are not read. A challenge ends the run as ``blocked``.

A row gives id, name, link, start / end, doors, the venue (name, street, postcode, town, ISO
country, coordinates), min / max price, currency, ticket status, minimum age, artists and the
artwork. Its start and end carry ``+00:00`` but are the venue's local clock (the page's own
JSON-LD gives ``+01:00`` for the same summer nights), so the clock is read in the venue's zone.
Rows have no genre: they are club nights (``default_genre``), and the title and description
carry the music.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, next_data, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

# The money of the countries Skiddle sells in, for a row that names none.
LOCAL_CURRENCY = {"GB": "GBP", "IE": "EUR"}


class SkiddleSettings(SourceSettings):
    site_url: str = "https://www.skiddle.com"
    # The kind page read in each city; its rows are club nights.
    kind_path: str = "clubs"
    # Skiddle's city slugs, only the UK destinations Israelis fly to most.
    cities: list[str] = Field(default_factory=lambda: ["London", "Manchester"])
    default_genre: str = "nightlife"
    sold_out_texts: list[str] = Field(default_factory=lambda: ["sold out"])


class SkiddleScraper(BaseScraper[SkiddleSettings]):
    name = "skiddle"
    settings_model = SkiddleSettings
    abroad_only = True

    def is_blocked(self, response: httpx.Response) -> bool:
        """AWS WAF's JS challenge: 202 with an empty body and ``x-amzn-waf-action``."""
        return bool(response.headers.get("x-amzn-waf-action")) or (
            response.status_code == 202 and not response.content.strip()
        )

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        seen: set[str] = set()
        listed = 0
        for city in s.cities:
            url = f"{s.site_url}/{s.kind_path}/{city}/"
            try:
                rows = city_rows(await self.get_text(url))
            except ParseError as exc:  # a changed page: the other city still counts
                self.record_failure(url, exc)
                continue
            listed += len(rows)
            for row in rows:
                try:
                    event = parse_event(row, s, self.timezone, city)
                except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                    self.record_failure(f"event {row.get('id')}", exc)
                    continue
                if event is None or event.source_event_id in seen:
                    continue
                seen.add(event.source_event_id)
                if is_current(event.start_at, event.end_at, retention, self.timezone):
                    yield event
        if not listed:
            raise ParseError("no city page lists an event")


def city_rows(page_html: str) -> list[dict[str, Any]]:
    """The events of a city kind page's first results (``initialResults.events``)."""
    props = (next_data(page_html).get("props") or {}).get("pageProps") or {}
    results = props.get("initialResults")
    if not isinstance(results, dict) or not isinstance(results.get("events"), list):
        raise ParseError("city page has no initialResults.events")
    return [r for r in results["events"] if isinstance(r, dict) and clean_str(r.get("id")) and clean_str(r.get("link"))]


def local_clock(value: Any, tz: str) -> datetime | None:
    """``2027-06-12T12:00:00+00:00`` as 12:00 in ``tz``: the offset on a row is not the venue's."""
    text = clean_str(value)
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text[:19])
    except ValueError:
        return None
    return naive.replace(tzinfo=zone(tz))


def spotify_url(uri: Any) -> str | None:
    """``spotify:artist:<id>`` as its open.spotify.com page."""
    text = clean_str(uri) or ""
    return f"https://open.spotify.com/artist/{text.rsplit(':', 1)[1]}" if text.startswith("spotify:artist:") else None


def parse_event(
    row: dict[str, Any], s: SkiddleSettings, timezone: str, page_city: str | None = None
) -> ScrapedEvent | None:
    """One listing row; None when cancelled. A venue whose town is a street (it has a number) is in
    the city of the page that listed it."""
    if str(row.get("cancelled") or "0") != "0":
        return None
    venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
    country = (clean_str(venue.get("country")) or "GB").upper()
    town = clean_str(venue.get("town")) or clean_str(venue.get("postcode_lookup"))
    city = town if town and not any(c.isdigit() for c in town) else page_city or town
    tz = local_zone(country, "Europe/London", city)
    start, end = local_clock(row.get("startdate"), tz), local_clock(row.get("enddate"), tz)
    title = clean_str(row.get("eventname"))
    if not title or not start:
        raise ParseError(f"skiddle {row.get('id')}: row without name or start")
    lat, lon = to_float(venue.get("latitude")), to_float(venue.get("longitude"))
    located = lat is not None and lon is not None and (lat, lon) != (0, 0)
    street = clean_str(venue.get("address"))
    postcode = clean_str(venue.get("postcode"))
    pricing = row.get("ticketpricing") if isinstance(row.get("ticketpricing"), dict) else {}
    low, high = to_decimal(pricing.get("minPrice")), to_decimal(pricing.get("maxPrice"))
    status = (clean_str(row.get("ticketStatusText")) or "").lower()
    artists = [a for a in row.get("artists") or [] if isinstance(a, dict) and clean_str(a.get("name"))]
    image = next(
        (clean_str(row.get(k)) for k in ("xlargeimageurl", "largeimageurl", "imageurl") if clean_str(row.get(k))), None
    )
    seller = clean_str(row.get("ticketUrl"))
    return ScrapedEvent(
        source=SkiddleScraper.name,
        source_event_id=str(row["id"]),
        url=row["link"],
        title=title,
        description=clean_str(row.get("description")),
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name")),
        address=", ".join(p for p in (street, postcode) if p) or None,
        city=city,
        country=country,
        time_zone=tz,
        latitude=lat if located else None,
        longitude=lon if located else None,
        source_genres=[s.default_genre],
        artists=[a["name"].strip() for a in artists],
        artist_profiles=[
            ArtistProfile(
                name=a["name"].strip(),
                source=SkiddleScraper.name,
                image_url=clean_str(a.get("image")),
                spotify_url=spotify_url(a.get("spotifyartisturl")),
            )
            for a in artists
        ],
        min_age=to_int(row.get("minage")) or None,
        images=[image] if image else [],
        currency=clean_str(row.get("eventCurrency"))
        or clean_str(row.get("currency"))
        or LOCAL_CURRENCY.get(country, "GBP"),
        min_price=low,
        max_price=high if high is not None and (low is None or high >= low) else low,
        is_sold_out=any(t in status for t in s.sold_out_texts),
        extra={"purchase_url": seller} if seller and seller.startswith("http") else {},
    )
