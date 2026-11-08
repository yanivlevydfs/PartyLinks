"""Standupist (standupist.co.il) - Israel's stand-up portal and box office, WordPress + EventON + WooCommerce.

The site's own EventON API, ``/wp-json/eventon/events``, gives every event in one call (~48, Nov 2026):
real UTC start and end, weekly repeats (``repeats``, one [start, end] per date), status, venue with
coordinates, regular and sale price, types, artwork and text. It holds drafts, cancelled shows and a
"TEST" event too, all skipped. It holds more than the listing page shows, so unlisted shows are read.

Two calls add the artists: ``/wp/v2/ajde_events`` gives each event's tag slugs (``class_list``) and
``/wp/v2/tags?slug=...`` their Hebrew names. Tags that name a recurring night and not a person are in
``not_artists``. The listing page ``/stand-up-show-tickets/`` is read once only for "sold out", which no
API gives (``.evo_soldout`` on the card of one date). Five requests per run at most for today's catalogue.

A show saved with its start equal to its end ("11:47 - 11:47") has no real time: date only. The API has
no city, and its area labels are loose (Ramat Gan under "Tel Aviv"), so the city comes from the address
or the venue name. BaBe Bar's own site links to these pages, so ``purchase_url`` merges the two listings.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from datetime import datetime
from functools import cache
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings, get_settings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, to_float, zone
from partylinks.places import PlaceIndex, all_cities
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class StandupistSettings(SourceSettings):
    site_url: str = "https://standupist.co.il"
    events_path: str = "/wp-json/eventon/events"
    listing_path: str = "/stand-up-show-tickets/"
    posts_path: str = "/wp-json/wp/v2/ajde_events"
    tags_path: str = "/wp-json/wp/v2/tags"
    page_size: int = 100
    published_status: str = "publish"
    kept_statuses: list[str] = Field(default_factory=lambda: ["scheduled", "rescheduled"])
    skip_titles: list[str] = Field(default_factory=lambda: ["TEST"])
    site_genre: str = "סטנדאפ"
    free_label: str = "ללא עלות כספית"
    # Tags of a recurring night (the house marathon, the women's line), not of a performer.
    not_artists: list[str] = Field(default_factory=lambda: ["portal-marathon", "lioness-roar"])
    # The site's spelling of a venue -> the name the venue's own source uses (one venue page).
    venue_names: dict[str, str] = Field(default_factory=dict)


class StandupistScraper(BaseScraper[StandupistSettings]):
    name = "standupist"
    settings_model = StandupistSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        payload = await self.get_json(s.site_url + s.events_path)
        rows = (payload.get("events") or {}) if isinstance(payload, dict) else {}
        if not rows:
            raise ParseError("the EventON API answered without events")

        page = await self.optional("listing page", self.get_text(s.site_url + s.listing_path))
        sold_out = parse_sold_out(page or "")
        artists = await self.optional("artists", self._artists()) or {}

        for row in rows.values():
            try:
                events = parse_event(row, s, self.timezone, sold_out, artists.get(row.get("id"), []))
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            for event in events:
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event

    async def _artists(self) -> dict[int, list[str]]:
        """Event id -> performers, from the tags of the WordPress posts."""
        s = self.settings
        slugs: dict[int, list[str]] = {}
        page = 1
        while True:
            posts = await self.get_json(
                s.site_url + s.posts_path, params={"per_page": s.page_size, "page": page, "_fields": "id,class_list"}
            )
            slugs |= {p["id"]: tag_slugs(p.get("class_list")) for p in posts}
            if len(posts) < s.page_size:
                break
            page += 1

        wanted = sorted({t for tags in slugs.values() for t in tags} - set(s.not_artists))
        names: dict[str, str] = {}
        for i in range(0, len(wanted), s.page_size):
            found = await self.get_json(
                s.site_url + s.tags_path,
                params={"per_page": s.page_size, "slug": ",".join(wanted[i : i + s.page_size]), "_fields": "slug,name"},
            )
            names |= {t["slug"]: html.unescape(t["name"]) for t in found}
        return {event: [names[t] for t in tags if t in names] for event, tags in slugs.items()}


def tag_slugs(classes: Any) -> list[str]:
    return [c.removeprefix("tag-") for c in classes or [] if c.startswith("tag-")]


def parse_sold_out(page: str) -> set[str]:
    """Card ids ("event_85455_0" = event 85455, date 0) the listing marks as sold out."""
    if not page:
        return set()
    return {
        card.attributes["id"] or ""
        for card in LexborHTMLParser(page).css(".eventon_list_event[id]")
        if card.css_first(".evo_soldout") is not None
    }


@cache
def _places() -> PlaceIndex:
    # The configured cities first, so "ירושלים" is Jerusalem and not one of GeoNames' other names for it.
    enrichment = get_settings().enrichment
    return PlaceIndex(all_cities(enrichment.cities, enrichment.places_min_population))


def find_city(address: str | None, venue: str | None) -> str | None:
    """The last place named: the town closes an address ("מצדה 2, אריאל"), a street may be named after one."""
    for text in (address, venue):
        if hits := _places().find(text):
            return hits[-1][1]
    return None


def _text(value: Any) -> str | None:
    """The API's values carry HTML entities and PHP's escaped quotes ("ג\\'ורג")."""
    return clean_str(html.unescape(str(value)).replace("\\", "")) if value else None


def _tiers(row: dict[str, Any], s: StandupistSettings, sold_out: bool) -> list[TicketTier]:
    regular = to_decimal(row.get("regular_price"))
    price = to_decimal(row.get("sale_price")) or regular
    status = TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE
    if price is None:
        kinds = (row.get("event_type_3") or {}).values()
        if s.free_label not in kinds:
            return []
        return [TicketTier(name="כניסה חופשית", price=to_decimal(0), is_free=True, status=status)]
    original = regular if regular is not None and regular > price else None
    return [TicketTier(name="כרטיס", price=price, original_price=original, is_free=price == 0, status=status)]


def parse_event(
    row: dict[str, Any], s: StandupistSettings, timezone: str, sold_out: set[str], artists: list[str]
) -> list[ScrapedEvent]:
    """One event per date; none for a draft, a cancelled show or the site's test event."""
    title = _text(row.get("name"))
    if (
        not title
        or title in s.skip_titles
        or row.get("post_status") != s.published_status
        or row.get("event_status") not in s.kept_statuses
    ):
        return []

    repeats = row.get("repeats") or []
    dates = repeats or [[row["start"], row["end"]]]
    base_url = str(row["permalink"]).split("var/")[0]
    venue = _text(row.get("location_name"))
    address = _text(row.get("location_address"))
    latitude, longitude = to_float(row.get("location_lat")), to_float(row.get("location_lon"))
    has_point = bool(latitude and longitude)
    all_day = row.get("all_day_event") == "yes"

    events = []
    for index, (start_ts, end_ts) in enumerate(dates):
        start: datetime = datetime.fromtimestamp(start_ts, zone(timezone))
        known = end_ts != start_ts and not all_day  # "11:47 - 11:47": saved without a real time
        url = f"{base_url}var/ri-{index}.l-L1/" if repeats else base_url
        tiers = _tiers(row, s, f"event_{row['id']}_{index}" in sold_out)
        prices = [t.price for t in tiers if t.price is not None]
        events.append(
            ScrapedEvent(
                source=StandupistScraper.name,
                source_event_id=f"{row['id']}-{start:%Y%m%d}" if repeats else str(row["id"]),
                url=url,
                title=title,
                description=html_to_text(row.get("details")) or _text(row.get("event_subtitle")),
                start_at=start if known else start.replace(hour=0, minute=0),
                end_at=datetime.fromtimestamp(end_ts, zone(timezone)) if known else None,
                time_known=known,
                venue_name=s.venue_names.get(venue, venue) if venue else None,
                address=address,
                city=find_city(address, venue),
                latitude=latitude if has_point else None,
                longitude=longitude if has_point else None,
                source_genres=[s.site_genre],  # its types are formats: "full show" would read as a concert
                artists=artists,
                image_url=row.get("image_url") or None,
                min_price=min(prices) if prices else None,
                max_price=max(prices) if prices else None,
                is_sold_out=bool(tiers) and tiers[0].status is TierStatus.SOLD_OUT,
                tickets=tiers,
                extra={"purchase_url": url, "show_types": list((row.get("event_type") or {}).values())},
            )
        )
    return events
