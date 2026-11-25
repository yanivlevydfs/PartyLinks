"""Eshkolot (eshkolot-il.com, "אשכולות - מרכזי הסיור והתודעה הישראלית") - a Lod-based network of touring and
heritage centres. Its public events are its festivals of guided tours across the country: "חנוכה ישראלי"
(/hanuka, ~70 tours with KKL on two or three days of Hanukkah, 10 / 45 ILS a participant) and "שבילי מנהיגות
ואחדות" (/manhigut, 4 free tours in Jerusalem and Tel Aviv, three days, two departures a day).

A hand-built static site (no CMS): every festival page lists its tours as ``article.f-tour`` cards (title,
place, text, meeting point, a Waze link to the meeting point, the sign-up link). Read every run:

- the sitemap and the home page's menu (``nav``): every page holding ``article.f-tour`` cards is a festival
  (new festivals are found without a config change);
- a festival's own script ``js/fest-<name>.js`` (``window.FEST``, built from the site's tour table): its days
  with the year, and per tour the days it runs, its hours ("10:30-13:30"), meeting point, type
  ("סיורי יום" / "סיורי ערב" / "הפנינג משפחות" / "סיורים באנגלית" / "סיורי בוטיק") and price; a tour marked
  ``off`` is dropped;
- a free-registration festival without that script (manhigut) has its days and departures in the sign-up
  form (``input[name=day]`` "1207" + its label "יום שני 7.12", ``input[name=time]`` "1000"), and its tour
  length in the text ("משך כל סיור 3 שעות");
- ``/api/availability?f=<festival>``: the seats left per tour and day (and departure): 0 = full.

One event per tour, day and departure. The meeting point's pin comes from the card's Waze link (a geohash
``waze.com/ul/h<hash>``, ``ll=lat,lon``, a Google Maps ``q=lat,lon``); other links (a Waze place id, a short
link) leave the meeting point and its town to geocoding. The town is the place the card names (a GeoNames
locality), else the nearest locality to the pin, else ``cities`` in config (nature sites: "המכתש הגדול").
The festival's genres are its name, "סיורים" and the tour's type. The pages' pictures are the festival's
artwork (og:image, the flyer); the cards have none.

Not read (no events): the vacations (nofesh-*: closed groups, bereaved families / reservists' wives, sign-up
closed), national service, the centres, guest houses, guides, videos, newsletter, terms pages, the English home
page (no tour pages of its own: the English tours are on /hanuka). No other platform sells these tours.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import CitySettings, RetentionSettings, get_settings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, og_image, zone
from partylinks.places import PlaceIndex, all_cities, latin_town
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class EshkolotSettings(SourceSettings):
    site_url: str = "https://www.eshkolot-il.com"
    sitemap_path: str = "/sitemap.xml"
    home_path: str = "/"
    availability_path: str = "/api/availability?f={group}"
    fest_script: str = r"js/fest-[\w-]+\.js"  # a festival's tour table
    fest_marker: str = "window.FEST ="
    free_hash: str = "#fReg"  # a free festival's sign-up form, on its own page
    duration_hours: float = 3.0  # a tour without hours of its own ("משך כל סיור 3 שעות")
    yearless_grace_days: int = 30  # a "7.12" without a year: the first year putting it after today less this
    # Loc / meeting-point words -> town, where the card names no locality and the pin has none nearby.
    cities: dict[str, str] = Field(default_factory=dict)
    nearest_town_km: float = 12.0  # a pin farther than this from every locality takes ``cities`` / none
    tour_genre: str = "סיורים"
    # Holidays a festival's title or heading names: its genre ("חנוכה ישראלי" is no Israeli music). The festival's
    # own name is no genre: "ישראלי" in it read as Israeli music.
    holiday_words: list[str] = Field(
        default_factory=lambda: ["חנוכה", "פסח", "סוכות", "שבועות", "פורים", 'ט"ו בשבט', "יום העצמאות", "ראש השנה"]
    )
    free_words: list[str] = Field(default_factory=lambda: ["ללא תשלום", "ללא עלות", "חינם"])
    under_free_age: int = 3  # "מתחת לגיל 3 חינם"
    organizer: str = "אשכולות"


@dataclass
class Tour:
    tid: str
    title: str
    loc: str | None = None
    sub: str | None = None
    text: str | None = None
    meet: str | None = None
    kind: str | None = None
    hours: str | None = None
    price: Decimal | None = None
    days: list[str] | None = None  # day ids ("1208"); None = every day of the festival
    buy: str | None = None
    lat: float | None = None
    lon: float | None = None
    off: bool = False


@dataclass
class Festival:
    url: str
    group: str
    name: str
    days: dict[str, date]  # "1208" -> 2026-12-08
    departures: list[str] = field(default_factory=list)  # "1000", "1300": the form's; none = tour hours
    duration_hours: float = 3.0
    free: bool = False
    price_note: str | None = None
    images: list[str] = field(default_factory=list)
    tours: list[Tour] = field(default_factory=list)
    holidays: list[str] = field(default_factory=list)  # the holidays its title names: genres


class EshkolotScraper(BaseScraper[EshkolotSettings]):
    name = "eshkolot"
    settings_model = EshkolotSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages = await self._pages()
        today = datetime.now(zone(self.timezone)).date()
        events: list[ScrapedEvent] = []
        for url in pages:
            html = await self.optional(f"page {url}", self.get_text(url))
            if not html or "f-tour" not in html:
                continue
            script = None
            if m := re.search(s.fest_script, html):
                script = await self.optional(f"script {m.group(0)}", self.get_text(urljoin(url, m.group(0))))
            try:
                fest = parse_festival(url, html, script, s, today)
            except Exception as exc:  # noqa: BLE001 - one odd festival page never drops the others
                self.record_failure(f"festival {url}", exc)
                continue
            if fest is None:
                continue
            left = await self.optional(
                f"availability {fest.group}",
                self.get_json(s.site_url + s.availability_path.format(group=fest.group), expect=dict),
            )
            seats = (left or {}).get("left") or {}
            for tour in fest.tours:
                try:
                    events.extend(tour_events(fest, tour, seats if isinstance(seats, dict) else {}, s, self.timezone))
                except Exception as exc:  # noqa: BLE001 - one bad card costs only its own tour
                    self.record_failure(f"tour {fest.group}-{tour.tid}", exc)
        current = [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]
        for event in sorted(current, key=lambda e: (e.start_at, e.source_event_id)):
            yield event

    async def _pages(self) -> list[str]:
        """Every page the sitemap or the home page's menu names, the site's own hosts only."""
        s = self.settings
        found: list[str] = []
        sitemap = await self.optional("sitemap", self.get_text(s.site_url + s.sitemap_path))
        found += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap or "")
        home = await self.optional("home page", self.get_text(s.site_url + s.home_path))
        if home:
            tree = LexborHTMLParser(home)
            found += [urljoin(s.site_url + "/", a.attributes.get("href") or "") for a in tree.css("nav a[href]")]
        host = urlparse(s.site_url).netloc
        out: dict[str, None] = {}
        for url in found:
            parts = urlparse(url)
            if parts.netloc == host and parts.path.strip("/"):
                out.setdefault(f"{s.site_url}/{parts.path.strip('/')}", None)
        return list(out)


def _text(node: LexborNode | None) -> str | None:
    return clean_str(node.text(separator=" ")) if node is not None else None


def _fest_json(script: str | None, marker: str) -> dict[str, Any] | None:
    if not script or (i := script.find(marker)) < 0:
        return None
    data, _ = json.JSONDecoder().raw_decode(script[i + len(marker) :].lstrip())
    return data if isinstance(data, dict) else None


def _yearless(day: int, month: int, today: date, grace_days: int) -> date:
    for year in (today.year, today.year + 1):
        d = date(year, month, day)
        if d >= today - timedelta(days=grace_days):
            return d
    return date(today.year + 1, month, day)


def parse_festival(url: str, html: str, script: str | None, s: EshkolotSettings, today: date) -> Festival | None:
    """A festival page (+ its ``fest-*.js`` table, when it has one) -> its days, departures and tours."""
    tree = LexborHTMLParser(html)
    cards = tree.css("article.f-tour")
    if not cards:
        return None
    fest = _fest_json(script, s.fest_marker) or {}
    path = urlparse(url).path.strip("/")
    group = str(fest.get("canonical") or "")
    if not group and (m := re.search(r"\bconst G\s*=\s*\"([\w-]+)\"", html)):
        group = m.group(1)
    group = group or path
    title = _text(tree.css_first("title")) or _text(tree.css_first("main h1")) or path
    name = clean_str(fest.get("name")) or clean_str(title.split("|")[0]) or path
    days: dict[str, date] = {}
    for d in fest.get("days") or []:
        if isinstance(d, dict) and d.get("id") and d.get("date"):
            days[str(d["id"])] = date.fromisoformat(str(d["date"]))
    if not days:  # the sign-up form: value "1207", label "יום שני 7.12"
        for box in tree.css("input[name=day]"):
            value = box.attributes.get("value") or ""
            if m := re.fullmatch(r"(\d{2})(\d{2})", value):
                days[value] = _yearless(int(m.group(2)), int(m.group(1)), today, s.yearless_grace_days)
    departures = [
        v for box in tree.css("input[name=time]") if re.fullmatch(r"\d{4}", v := box.attributes.get("value") or "")
    ]
    page_text = tree.body.text(separator=" ") if tree.body else ""
    duration = s.duration_hours
    if m := re.search(r"משך (?:כל )?(?:ה)?סיור\s*(?:כ-?)?(\d+(?:\.\d+)?)\s*שעות", page_text):
        duration = float(m.group(1))
    price_box = _text(tree.css_first(".f-pass-price")) or ""
    # A table gives each tour its price; a page without one is free when its sign-up says so (manhigut).
    free = not fest and (bool(tree.css_first(".f-free")) or any(w in price_box for w in s.free_words))
    images = [i for i in (og_image(html),) if i]
    for img in tree.css("figure.f-poster img[src]"):
        images.append(urljoin(url, img.attributes.get("src") or ""))
    rows = {str(t.get("id")): t for t in fest.get("tours") or [] if isinstance(t, dict)}
    tours = [parse_card(card, rows, url, s) for card in cards]
    return Festival(
        url=url,
        group=group,
        name=name,
        holidays=[w for w in s.holiday_words if w in f"{title} {name} {_text(tree.css_first('main h1')) or ''}"],
        days=days,
        departures=departures,
        duration_hours=duration,
        free=free,
        price_note=clean_str(fest.get("priceNote")),
        images=list(dict.fromkeys(images)),
        tours=[t for t in tours if t is not None],
    )


def parse_card(card: LexborNode, rows: dict[str, dict[str, Any]], page_url: str, s: EshkolotSettings) -> Tour | None:
    """One ``article.f-tour`` card, with its row of the festival table when there is one."""
    tid = (card.attributes.get("id") or "").removeprefix("t-")
    if not tid:
        return None
    row = rows.get(tid, {})
    summary = card.parent.css_first("summary .f-tname") if card.parent is not None else None
    loc_node = summary.css_first(".f-tloc") if summary is not None else None
    loc = clean_str((_text(loc_node) or "").strip(" |")) if loc_node is not None else None
    name = None
    if summary is not None:
        name = clean_str(summary.text(deep=False)) or clean_str(
            (_text(summary) or "").replace(_text(loc_node) or "", "")
        )
    title = clean_str(row.get("title")) or name
    if not title:
        return None
    meet = None
    for li in card.css(".f-facts li"):
        label = _text(li.css_first("b")) or ""
        if label.startswith("נפגשים"):
            meet = clean_str((_text(li.css_first("span")) or "").replace(label, "", 1))
    paragraphs = [t for p in card.css(".f-text p") if (t := _text(p))]
    buy = None
    for a in card.css(".f-foot a[href]"):
        href = a.attributes.get("href") or ""
        if "f-waze" in (a.attributes.get("class") or ""):
            continue
        buy = urljoin(page_url, href) if not href.startswith("#") else page_url + href
        break
    waze = card.css_first("a.f-waze[href]")
    lat, lon = map_point(waze.attributes.get("href") if waze is not None else None)
    price = row.get("price")
    tour_days = row.get("days")
    return Tour(
        tid=tid,
        title=title,
        loc=clean_str(row.get("loc")) or loc,
        sub=clean_str(row.get("sub")) or _text(card.css_first(".f-sub")),
        text="\n\n".join(paragraphs) or None,
        meet=clean_str(row.get("meet")) or meet,
        kind=clean_str(row.get("type")),
        hours=clean_str(row.get("hours")),
        price=Decimal(str(price)) if isinstance(price, (int, float)) else None,
        days=[str(d) for d in tour_days] if isinstance(tour_days, list) else None,
        buy=buy,
        lat=lat,
        lon=lon,
        off=bool(row.get("off")) or card.attributes.get("data-status") == "off",
    )


_GEOHASH = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_point(code: str) -> tuple[float, float] | None:
    """Decode a geohash (Waze's ``/ul/h<hash>`` links) to the centre of its cell."""
    lat, lon = [-90.0, 90.0], [-180.0, 180.0]
    even = True
    for ch in code.lower():
        if (value := _GEOHASH.find(ch)) < 0:
            return None
        for bit in (16, 8, 4, 2, 1):
            box = lon if even else lat
            mid = (box[0] + box[1]) / 2
            box[0 if value & bit else 1] = mid
            even = not even
    return (lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2


def map_point(href: str | None) -> tuple[float | None, float | None]:
    """The meeting point a Waze / Google Maps link pins; (None, None) for a place id or a short link."""
    if not href:
        return None, None
    parts = urlparse(href)
    point = None
    if m := re.fullmatch(r"/ul/h([0-9b-hjkmnp-z]{6,12})", parts.path):
        point = geohash_point(m.group(1))
    else:
        query = parse_qs(parts.query)
        for key in ("ll", "q"):
            value = unquote((query.get(key) or [""])[0])
            if m := re.fullmatch(r"\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*", value):
                point = float(m.group(1)), float(m.group(2))
                break
    if point is None or not (29 < point[0] < 34 and 34 < point[1] < 36.5):  # Israel only: a bad link pins nothing
        return None, None
    return round(point[0], 6), round(point[1], 6)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


@lru_cache(maxsize=1)
def _towns() -> tuple[list[CitySettings], PlaceIndex]:
    """The site's own city list (configured cities + GeoNames towns over ``places_min_population``):
    canonical names ("ירושלים", "תל אביב-יפו"), not GeoNames' first Hebrew alias."""
    enrichment = get_settings().enrichment
    towns = all_cities(enrichment.cities, enrichment.places_min_population)
    return towns, PlaceIndex(towns)


def tour_city(tour: Tour, s: EshkolotSettings) -> str | None:
    """The configured town for the tour's place, else the locality the card names, else the nearest to the pin."""
    for text in (tour.loc, tour.meet, tour.sub):
        for word, city in s.cities.items():
            if text and word in text:
                return city
    towns, index = _towns()
    for text in (tour.loc, tour.meet, tour.sub):
        if hits := index.find(text):
            return hits[-1][1]  # "צפת העתיקה" -> צפת; "גבעת קובי, שדרות" -> שדרות
        if text and (latin := latin_town(text)):  # "Jaffa": the canonical name of its town
            return next((c for _, c in index.find(latin[1])), latin[1])
    if tour.lat is not None and tour.lon is not None:
        point = (tour.lat, tour.lon)
        best = min(towns, key=lambda t: _km(point, t.center) if t.center else math.inf, default=None)
        if best is not None and best.center and _km(point, best.center) <= s.nearest_town_km:
            return best.he
    return None


def _clock(value: str) -> tuple[int, int]:
    h, m = value.replace(":", "")[:-2], value.replace(":", "")[-2:]
    return int(h), int(m)


def tour_events(
    fest: Festival, tour: Tour, seats: dict[str, Any], s: EshkolotSettings, timezone: str
) -> list[ScrapedEvent]:
    """One event per day and departure of one tour."""
    if tour.off:
        return []
    tz = zone(timezone)
    city = tour_city(tour, s)
    address = ", ".join(p for p in (tour.meet, city if city and city not in (tour.meet or "") else None) if p) or None
    slots: list[tuple[str | None, str, str | None]] = []  # (departure id, start, end)
    if tour.hours and (m := re.fullmatch(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", tour.hours)):
        slots.append((None, m.group(1), m.group(2)))
    else:
        slots += [(d, d, None) for d in fest.departures]
    genres = [g.lower() for g in dict.fromkeys(x for x in (*fest.holidays, s.tour_genre, tour.kind) if x)]
    free = fest.free or tour.price == 0
    description = "\n\n".join(p for p in (tour.sub, tour.text, f"נפגשים ב: {tour.meet}" if tour.meet else None) if p)
    out = []
    for day_id in tour.days or list(fest.days):
        day = fest.days.get(day_id)
        if day is None:
            continue
        for dep, start_s, end_s in slots:
            sh, sm = _clock(start_s)
            start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
            if end_s:
                eh, em = _clock(end_s)
                end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
            else:
                end = start + timedelta(hours=fest.duration_hours)
            key = "-".join(p for p in (fest.group, tour.tid, day_id, dep) if p)
            left = seats.get(key)
            remaining = left if isinstance(left, int) else None
            sold_out = remaining == 0
            tiers = []
            if free:
                tiers.append(
                    TicketTier(
                        name="הרשמה ללא עלות", price=Decimal(0), is_free=True, remaining=remaining, is_sold_out=sold_out
                    )
                )
            elif tour.price is not None:
                tiers.append(
                    TicketTier(
                        name="למשתתף",
                        description=fest.price_note,
                        price=tour.price,
                        remaining=remaining,
                        is_sold_out=sold_out,
                    )
                )
                tiers.append(
                    TicketTier(
                        name=f"מתחת לגיל {s.under_free_age}", price=Decimal(0), is_free=True, is_sold_out=sold_out
                    )
                )
            prices = [t.price for t in tiers if t.price is not None]
            out.append(
                ScrapedEvent(
                    source=EshkolotScraper.name,
                    source_event_id=key,
                    url=f"{fest.url}#t-{tour.tid}",
                    title=tour.title,
                    description=description or None,
                    start_at=start,
                    end_at=end,
                    venue_name=tour.meet or tour.loc,
                    address=address,
                    city=city,
                    latitude=tour.lat,
                    longitude=tour.lon if tour.lat is not None else None,
                    source_genres=genres,
                    images=list(fest.images),
                    tickets=tiers,
                    currency="ILS",
                    min_price=min(prices) if prices else None,
                    max_price=max(prices) if prices else None,
                    is_sold_out=sold_out,
                    organizer=s.organizer,
                    extra={"purchase_url": tour.buy} if tour.buy else {},
                )
            )
    return out
