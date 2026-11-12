"""The worker's pass, on a thread, one request at a time: read Ontopo's venues (new, changed or stale only),
match every place to one, and read own websites for places
still missing hours or a phone. Everything is committed as it goes, so a restart continues where it was.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import distinct_on

from partylinks.db import Database, EventRow, dialect_insert, upcoming_filter, utcnow, with_db_retry
from partylinks.placecards import IS_PLACE
from partylinks.placeinfo.config import PlaceInfoSettings
from partylinks.placeinfo.models import OntopoVenueRow, PlaceInfoRow
from partylinks.placeinfo.pages import ontopo_page, ontopo_sitemap, phone_key, site_details
from partylinks.venue_profiles import _distance_m, _joined

log = logging.getLogger(__name__)

_CELL_DEG = 0.02  # ~2 km, wider than either radius: a venue is looked up in its cell and the 8 around it


@dataclass
class Place:
    url: str
    name: str
    lat: float | None
    lng: float | None
    phone: str | None
    slug: str | None = None  # the Ontopo link on the place's own page
    site: str | None = None  # its website, profiles left out
    has_hours: bool = False  # on its own page


@dataclass
class Venue:
    slug: str
    name: str
    lat: float | None
    lng: float | None
    phone: str | None


def _cell(lat: float, lng: float) -> tuple[int, int]:
    return int(lat // _CELL_DEG), int(lng // _CELL_DEG)


# Hebrew and Latin letters to one consonant skeleton: "רוסו" and "ROSSO" -> "rs", "לולו 47" and "Loulou 47" -> "ll47".
_SKELETON = str.maketrans({
    **dict(zip("בגדזחטכלמנסעפצקרשתךםןףץ", "bgdzhtklmnsxpckrstkmnpc", strict=True)),
    "c": "s", "q": "k", "v": "b", "w": "", "f": "p", "x": "ks", "ו": "", "י": "", "א": "", "ה": "", "ע": "", "a": "", "e": "", "i": "", "o": "", "u": "", "y": "", "h": "",
})  # fmt: skip


def _skeleton(name: str) -> str:
    return re.sub(r"(.)\1+", r"\1", _joined(name).translate(_SKELETON))


def _same_spelling(a: str, b: str, need: float) -> bool:
    x, y = _joined(a), _joined(b)
    if len(x) < 3 or len(y) < 3:
        return bool(x) and x == y
    return SequenceMatcher(None, x, y).ratio() >= need


def _same_sound(a: str, b: str, need: float) -> bool:
    """A phone is shared by a restaurateur's places, so its name must fit too, in either alphabet:
    the whole skeleton, or one inside the other ("קפה נואר" / "נואר")."""
    x, y = _skeleton(a), _skeleton(b)
    if not x or not y:
        return False
    if len(x) >= 3 and len(y) >= 3 and (x in y or y in x):
        return True
    return SequenceMatcher(None, x, y).ratio() >= need


def match_places(places: Iterable[Place], venues: Iterable[Venue], cfg: PlaceInfoSettings) -> dict[str, tuple[str, str]]:
    """{place url: (slug, how)}: the page's own link, else a like name close by, else the nearest venue
    with its phone and a name that sounds alike."""
    grid: dict[tuple[int, int], list[Venue]] = {}
    for v in venues:
        if v.lat is not None and v.lng is not None:
            grid.setdefault(_cell(v.lat, v.lng), []).append(v)
    out: dict[str, tuple[str, str]] = {}
    for p in places:
        if p.slug:
            out[p.url] = (p.slug, "link")
            continue
        if p.lat is None or p.lng is None:
            continue
        here = (p.lat, p.lng)
        cx, cy = _cell(p.lat, p.lng)
        near = [
            (_distance_m(here, (v.lat, v.lng)), v)  # type: ignore[arg-type]
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for v in grid.get((cx + dx, cy + dy), [])
        ]
        near.sort(key=lambda d: d[0])
        if by_name := next((v for d, v in near if d <= cfg.name_radius_m and _same_spelling(p.name, v.name, cfg.name_similarity)), None):
            out[p.url] = (by_name.slug, "name")
        elif by_phone := next(
            (v for d, v in near if p.phone and v.phone == p.phone and d <= cfg.phone_radius_m and _same_sound(p.name, v.name, cfg.sound_similarity)),
            None,
        ):
            out[p.url] = (by_phone.slug, "phone")
    return out


def _public_host(request: httpx.Request) -> None:
    """A website address comes from another site: nothing inside our network, redirects included."""
    infos = socket.getaddrinfo(request.url.host, request.url.port or 443, proto=socket.IPPROTO_TCP)
    if not all(ipaddress.ip_address(str(i[4][0]).split("%")[0]).is_global for i in infos):
        raise httpx.RequestError(f"not a public host: {request.url.host}", request=request)


class _Reader:
    """Polite GETs: one at a time with a delay, ending the pass after too many failures in a row."""

    def __init__(self, cfg: PlaceInfoSettings, stop: threading.Event) -> None:
        self.cfg, self.stop = cfg, stop
        self.client = httpx.Client(headers={"user-agent": cfg.user_agent}, timeout=cfg.timeout_seconds, follow_redirects=True)
        self.failures = 0
        self.status: int | None = None  # the last answer's HTTP status; None when nothing answered

    def done(self) -> bool:
        return self.stop.is_set() or self.failures >= self.cfg.max_consecutive_failures

    def get(self, url: str, limit: int | None = None) -> bytes | None:
        """The body, cut at ``limit`` bytes; empty for a page that is gone; None when the request failed."""
        time.sleep(self.cfg.delay_seconds)
        self.status = None
        try:
            with self.client.stream("GET", url) as response:
                self.status = response.status_code
                if response.status_code in (404, 410):
                    self.failures = 0
                    return b""
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if limit and len(body) >= limit:
                        break
        except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:  # OSError: a name that doesn't resolve
            self.failures += 1
            log.info("place info: %s: %s", url, exc)
            return None
        self.failures = 0
        return bytes(body)


def _upsert(db: Database, model: Any, rows: list[dict[str, Any]], keys: list[str]) -> None:
    def write() -> None:
        with db.session() as session:
            for row in rows:
                stmt = dialect_insert(session, model).values(row)
                update = {c: stmt.excluded[c] for c in row if c not in keys}
                session.execute(stmt.on_conflict_do_update(index_elements=keys, set_=update))

    if rows:
        with_db_retry("place info", write)


def load_places(db: Database, cfg: PlaceInfoSettings) -> list[Place]:
    """Each place once, from one upcoming listing per URL."""
    with db.session(write=False) as session:
        rows = session.execute(
            select(EventRow.url, EventRow.venue_name, EventRow.title, EventRow.latitude, EventRow.longitude, EventRow.extra)
            .where(upcoming_filter(), IS_PLACE)
            .ext(distinct_on(EventRow.url))
            .order_by(EventRow.url, EventRow.start_at)
        ).all()
    places = []
    for url, venue, title, lat, lng, extra in rows:
        links = extra.get("links") or [extra.get("website")]
        site = next((u for u in links if u and not any(h in urlsplit(u).netloc for h in cfg.social_hosts)), None)
        places.append(Place(url, venue or title, lat, lng, phone_key(extra.get("phone")), extra.get("ontopo_slug"), site, bool(extra.get("hours"))))
    return places


def read_venues(db: Database, cfg: PlaceInfoSettings, reader: _Reader, extra_slugs: Iterable[str]) -> int:
    """Pages that are new, changed in the sitemap or older than ``refresh_days``, plus slugs places link to."""
    sitemap = reader.get(cfg.ontopo_sitemap)
    pages: dict[str, str | None] = ontopo_sitemap(sitemap.decode("utf8", "replace")) if sitemap else {}
    for slug in extra_slugs:
        pages.setdefault(slug, None)
    with db.session(write=False) as session:
        known = {r.slug: r for r in session.execute(select(OntopoVenueRow.slug, OntopoVenueRow.lastmod, OntopoVenueRow.fetched_at))}
    stale = utcnow() - timedelta(days=cfg.refresh_days)
    todo = [s for s, m in pages.items() if s not in known or known[s].fetched_at < stale or (m and known[s].lastmod != m)]
    read = 0
    for slug in todo:
        if reader.done():
            break
        html = reader.get(cfg.ontopo_page.format(slug=slug))
        row = _parsed(html, slug)
        if row is None and html is None and (reader.status or 0) >= 500:
            # Ontopo answers this page with its own server error, every pass (12874624: 500 for weeks): marked read,
            # data kept, so it is asked again only when its lastmod changes or after refresh_days.
            _upsert(db, OntopoVenueRow, [{"slug": slug, "lastmod": pages[slug], "fetched_at": utcnow()}], ["slug"])
            continue
        if row is None:  # no answer, or a page without the state (a block page): not stored, asked next pass
            reader.failures += html is not None
            continue
        # A page with no venue is kept as an empty row, so it is not asked again before refresh_days.
        empty = {"title": None, "latitude": None, "longitude": None, "phone": None, "address": None, "website": None, "tags": None, "hours": None}
        _upsert(db, OntopoVenueRow, [{**empty, **row, "slug": slug, "lastmod": pages[slug], "fetched_at": utcnow()}], ["slug"])
        read += 1
    return read


def _parsed(html: bytes | None, slug: str) -> dict[str, Any] | None:
    if html is None:
        return None
    try:
        return ontopo_page(html.decode("utf8", "replace")) if html else {}
    except (ValueError, AttributeError, TypeError) as exc:  # the page's state changed: logged, the others go on
        log.warning("ontopo %s: %s", slug, exc)
        return None


def save_matches(db: Database, places: list[Place], cfg: PlaceInfoSettings) -> dict[str, tuple[str, str]]:
    with db.session(write=False) as session:
        venues = [Venue(slug, title or "", lat, lng, phone) for slug, title, lat, lng, phone in session.execute(select(
            OntopoVenueRow.slug, OntopoVenueRow.title, OntopoVenueRow.latitude, OntopoVenueRow.longitude, OntopoVenueRow.phone,
        ).where(OntopoVenueRow.title.is_not(None), OntopoVenueRow.title.not_like(f"{cfg.ontopo_event_prefix}%")))]  # fmt: skip
    matched = match_places(places, venues, cfg)
    _upsert(
        db, PlaceInfoRow,
        [{"url": p.url, "ontopo_slug": matched.get(p.url, (None, None))[0], "matched_by": matched.get(p.url, (None, None))[1]} for p in places],
        ["url"],
    )  # fmt: skip

    def drop_gone() -> None:  # places no longer listed
        with db.session() as session:
            session.execute(delete(PlaceInfoRow).where(PlaceInfoRow.url.not_in([p.url for p in places])))

    if places:
        with_db_retry("place info", drop_gone)
    return matched


def read_sites(db: Database, cfg: PlaceInfoSettings, stop: threading.Event, places: list[Place], matched: dict[str, tuple[str, str]]) -> int:
    """Own websites' JSON-LD, one page each, every ``refresh_days``: only places with a website that still
    lack hours or a phone (from their own page or their Ontopo venue)."""
    with db.session(write=False) as session:
        checked = dict(session.execute(select(PlaceInfoRow.url, PlaceInfoRow.site_checked_at)).all())
        venue_hours = set(session.scalars(select(OntopoVenueRow.slug).where(OntopoVenueRow.hours.is_not(None))))
    stale = utcnow() - timedelta(days=cfg.refresh_days)
    todo = [
        p for p in places
        if p.site and not ((when := checked.get(p.url)) is not None and when >= stale)
        and not (p.phone and (p.has_hours or matched.get(p.url, ("", ""))[0] in venue_hours))
    ]  # fmt: skip
    # A separate client: only here do addresses come from another site, so only here they are checked.
    reader = _Reader(cfg, stop)
    reader.client.event_hooks = {"request": [_public_host]}
    found = 0
    for place in todo:
        if reader.done():
            break
        html = reader.get(place.site or "", cfg.website_max_bytes)
        details = None
        if html:
            try:
                details = site_details(html.decode("utf8", "replace"))
            except (ValueError, AttributeError, TypeError) as exc:  # anyone's markup
                log.info("place info: %s: %s", place.site, exc)
        found += bool(details)
        _upsert(db, PlaceInfoRow, [{"url": place.url, "site": details, "site_checked_at": utcnow()}], ["url"])
    return found


def run_pass(db: Database, cfg: PlaceInfoSettings, stop: threading.Event) -> None:
    reader = _Reader(cfg, stop)
    places = load_places(db, cfg)
    read = read_venues(db, cfg, reader, {p.slug for p in places if p.slug})
    matched = save_matches(db, places, cfg)
    sites = read_sites(db, cfg, stop, places, matched)
    log.info("place info: %d places, %d matched, %d venue pages read, %d sites with details",
             len(places), len(matched), read, sites)  # fmt: skip

