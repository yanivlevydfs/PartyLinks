"""Israel Nature and Parks Authority (parks.org.il): guided tours, family activities, shows and
workshops in national parks and nature reserves, booked by date.

robots.txt allows everything read here. The site is WordPress:

* ``get_activity_board_api`` (one request, a JSON-encoded string): the activity board, ~490 items
  with title, park, description, price text, image and the Checkfront item id.
* ``/wp/v2/activity`` (100 a page): each activity's page link, park (``acf.rpSelect``), kind
  ("סיור מודרך") and ages, matched to the board by the Checkfront id.
* ``/wp/v2/rp`` (100 a page): the parks, with coordinates in the Israeli grid (ITM, EPSG:2039) and
  the entry fee table (``rp_sales_table``).

Price: the activity's ``act_price`` (kind, own price fields, the text under them). Most activities are
"ללא / בתשלום נוסף על דמי הכניסה לאתר": they cost the park's entry fee (plus the add-on the text names).

Dates come from the authority's public Checkfront booking pages: ``/reserve/inventory/`` gives one
month of an item's calendar (``calendar_data``: 1 = open for booking), and ``/reserve/item/`` for
one date gives its time slots and the slot length. So a run is one calendar request per item and
month, plus one per open date. Most items have no open date (self-guided visits, past seasons).
One event per item and date: the first slot to the end of the last one.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import Field
from pyproj import Transformer

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_prices

_SLOT = re.compile(r"cf-timeslot-button ([\w-]+)[^>]*>\s*<input[^>]*>\s*(\d{1,2}:\d{2})")
_LENGTH = re.compile(r"name='slip' value='[^']*@\d{1,2}:\d{2}X(\d+)")


class ParksSettings(SourceSettings):
    site_url: str = "https://www.parks.org.il"
    board_path: str = "/wp-json/wp/v2/get_activity_board_api"
    activities_path: str = "/wp-json/wp/v2/activity"
    parks_path: str = "/wp-json/wp/v2/rp"
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(20, ge=1)
    checkfront_url: str = "https://checkfrontcom.checkfront.com"
    calendar_path: str = "/reserve/inventory/"
    date_path: str = "/reserve/item/"
    booking_path: str = "/reserve/?item_id={id}&lang_id=he"
    months_ahead: int = Field(6, ge=1)
    grid_epsg: int = 2039  # the parks' coordinates: Israeli Transverse Mercator
    # Every activity is outdoors in a park: a category for the genres beside the site's own kind.
    category: str = "טבע"
    # The price text of an activity with nothing to pay at all (most say "no extra charge" over the park's fee).
    free_price: str = "ללא תשלום"
    # Kinds that are no event: a visit on your own or with an audio guide, open every day (~3,000 dates).
    skip_kinds: list[str] = Field(default_factory=lambda: ["פעילות עצמאית", "מדריך קולי"])
    # The same visits when the activity names no kind ("המדריך הקולי Trip-Talk"): by the board title.
    skip_title_words: list[str] = Field(default_factory=lambda: ["מדריך הקולי", "מדריך קולי", "trip-talk", "trip talk"])


class ParksScraper(BaseScraper[ParksSettings]):
    name = "parks"
    settings_model = ParksSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        board = json.loads(await self.get_json(s.site_url + s.board_path, expect=str))
        activities = {a["cf"]: a for a in await self._pages(s.activities_path, _ACTIVITY_FIELDS, parse_activity)}
        parks = {p["id"]: p for p in await self._pages(s.parks_path, _PARK_FIELDS, self._park)}
        items = [
            i for i in board.values()
            if isinstance(i, dict) and (cf := clean_str(i.get("chackfront_id")))
            and not set(activities.get(cf, {}).get("kinds", [])) & set(s.skip_kinds)
            and not any(w in (clean_str(i.get("title")) or "").lower() for w in s.skip_title_words)
        ]  # fmt: skip
        today = datetime.now(zone(self.timezone)).date()
        months = [_month(today, n) for n in range(s.months_ahead)]
        open_days: set[tuple[str, date]] = set()
        async for days in self.stream_isolated(
            [(str(i["chackfront_id"]), m) for i in items for m in months],
            self._calendar,
            label=lambda p: f"{p[0]} {p[1]}",
        ):
            open_days |= {d for d in days if d[1] >= today}
        by_id = {str(i["chackfront_id"]): i for i in items}
        retention = RetentionSettings()
        async for event in self.stream_isolated(
            sorted(open_days),
            lambda d: self._date(by_id[d[0]], d[1], activities, parks),
            label=lambda d: f"{d[0]} {d[1]}",
        ):
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                yield event

    async def _pages(self, path: str, fields: str, parse: Any) -> list[dict[str, Any]]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_fields": fields}
            response = await self.request("GET", s.site_url + path, params=params)
            return [r for row in response.json() if (r := parse(row))], int(response.headers.get("x-wp-total") or 0)

        return await self.paginate(page, s.per_page, s.max_pages)

    def _park(self, row: dict[str, Any]) -> dict[str, Any] | None:
        return parse_park(row, self.settings.grid_epsg)

    async def _calendar(self, key: tuple[str, date]) -> list[tuple[str, date]]:
        item, month = key
        s = self.settings
        params = {"item_id": item, "start_date": month.isoformat(), "end_date": month.isoformat()}
        data = await self.get_json(s.checkfront_url + s.calendar_path, params=params, expect=dict)
        return [(item, day) for day in open_dates(data)]

    async def _date(
        self, item: dict[str, Any], day: date, activities: dict[str, dict[str, Any]], parks: dict[int, dict[str, Any]]
    ) -> ScrapedEvent | None:
        s = self.settings
        params = {"item_id": item["chackfront_id"], "start_date": day.isoformat(), "end_date": day.isoformat()}
        page = await self.get_text(s.checkfront_url + s.date_path, params=params)
        return parse_event(item, day, page, activities, parks, s, self.timezone)


_ACTIVITY_FIELDS = (
    "id,link,acf.rpSelect,acf.checkfront_id,acf.activity_type_home,acf.act_important.act_important_age,acf.act_price"
)
_PARK_FIELDS = "id,title,acf.lat_lng,acf.long_lng,acf.rp_sales_table"
_AMOUNT = re.compile(r"\d+(?:\.\d+)?")
# The activity's own price fields, beside the text under them.
_PRICE_FIELDS = {
    "act_price_one": "כרטיס", "act_adult_price": "מבוגר", "act_child_price": "ילד", "act_discounted_price": "הנחה",
}  # fmt: skip


def _month(today: date, n: int) -> date:
    """The first day of the n-th month from today's (today itself for n = 0)."""
    if not n:
        return today
    total = today.year * 12 + today.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


@cache
def _grid(epsg: int) -> Transformer:
    return Transformer.from_crs(epsg, 4326, always_xy=True)


def parse_activity(row: dict[str, Any]) -> dict[str, Any] | None:
    acf = row.get("acf") if isinstance(row.get("acf"), dict) else {}
    cf = clean_str(acf.get("checkfront_id"))
    if not cf:
        return None
    kind = acf.get("activity_type_home")
    label = clean_str(kind.get("label")) if isinstance(kind, dict) else None
    ages = (
        (acf.get("act_important") or {}).get("act_important_age")
        if isinstance(acf.get("act_important"), dict)
        else None
    )
    price = acf.get("act_price") if isinstance(acf.get("act_price"), dict) else {}
    return {
        "cf": cf,
        "price": price,
        "link": clean_str(row.get("link")),
        "park": acf.get("rpSelect") if isinstance(acf.get("rpSelect"), int) else None,
        "kinds": [k for k in [label, *(ages if isinstance(ages, list) else [])] if isinstance(k, str) and "/" not in k],
    }


def parse_park(row: dict[str, Any], epsg: int) -> dict[str, Any] | None:
    acf = row.get("acf") if isinstance(row.get("acf"), dict) else {}
    try:
        x, y = float(acf.get("lat_lng") or ""), float(acf.get("long_lng") or "")
    except ValueError:
        return None
    lng, lat = _grid(epsg).transform(x, y)  # the site names the easting "lat_lng"
    if not (29 < lat < 34 and 34 < lng < 36):
        return None
    return {"id": row.get("id"), "latitude": round(lat, 6), "longitude": round(lng, 6), "entry": entry_fees(acf)}


def entry_fees(acf: dict[str, Any]) -> list[TicketTier]:
    """The park's entry fee table ("דמי כניסה": מבוגר 16.00 ₪, ילד 7.00 ₪, ...); a group's free row
    (soldiers: "ללא תשלום") is no free entry for everyone, so only priced rows count."""
    tables = [t for t in acf.get("rp_sales_table") or [] if isinstance(t, dict)]
    table = next((t for t in tables if "כניסה" in str(t.get("rp_sales_tab_title"))), tables[0] if tables else {})
    tiers = []
    for row in table.get("table") or []:
        amount = _AMOUNT.search(str(row.get("price") or "")) if isinstance(row, dict) else None
        if amount:
            name = f"דמי כניסה - {clean_str(row.get('type')) or 'כרטיס'}"
            tiers.append(TicketTier(name=name, price=Decimal(amount.group())))
    return tiers


def price_tiers(price: dict[str, Any], entry: list[TicketTier], free_price: str) -> list[TicketTier]:
    """What taking part costs: free; the activity's own price (entry included or not); or the park's
    entry fee, plus the activity's add-on, when it is "ללא / בתשלום נוסף על דמי הכניסה לאתר"."""
    kind = clean_str(price.get("act_price_type")) or ""
    if kind == free_price:
        return [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)]
    own = [
        TicketTier(name=name, price=Decimal(m.group()))
        for key, name in _PRICE_FIELDS.items()
        if (m := _AMOUNT.search(str(price.get(key) or "")))
    ] + [TicketTier(name="כרטיס", price=p) for p in text_prices(clean_str(price.get("act_text_bottom_price")))]
    if "דמי הכניסה" in kind and "כלולים" not in kind:
        return [*entry, *(t.model_copy(update={"name": f"תוספת - {t.name}"}) for t in own)] if entry else []
    return own


def open_dates(data: dict[str, Any]) -> list[date]:
    """The days of a calendar month open for booking."""
    days = data.get("calendar_data") if isinstance(data.get("calendar_data"), dict) else {}
    return [datetime.strptime(k, "%Y%m%d").date() for k, v in days.items() if v and re.fullmatch(r"\d{8}", k)]


def _image(url: str | None) -> str | None:
    """The board's thumbnails go through imgp.php?src=<the real file>."""
    if not url:
        return None
    src = parse_qs(urlsplit(url).query).get("src")
    return src[0] if src else url


def parse_event(
    item: dict[str, Any],
    day: date,
    page: str,
    activities: dict[str, dict[str, Any]],
    parks: dict[int, dict[str, Any]],
    s: ParksSettings,
    timezone: str,
) -> ScrapedEvent | None:
    cf = str(item["chackfront_id"])
    title, place = clean_str(item.get("title")), clean_str(item.get("place"))
    if not title:
        return None
    activity = activities.get(cf, {})
    park = parks.get(activity.get("park") or -1, {})
    slots = [(state, clock) for state, clock in _SLOT.findall(page)]
    tz = zone(timezone)
    if slots:
        first, last = (datetime.strptime(c, "%H:%M").time() for c in (slots[0][1], slots[-1][1]))
        length = _LENGTH.search(page)
        start = datetime.combine(day, first, tz)
        end = datetime.combine(day, last, tz) + timedelta(minutes=int(length.group(1))) if length else None
    else:
        start, end = datetime.combine(day, datetime.min.time(), tz), None
    price = clean_str(item.get("price"))
    tiers = price_tiers(activity.get("price") or {"act_price_type": price}, park.get("entry") or [], s.free_price)
    priced = [t.price for t in tiers if t.price is not None]
    link = activity.get("link") or (clean_str(item.get("link")) or "").split("#")[0]
    image = _image(clean_str(item.get("image")))
    return ScrapedEvent(
        source=ParksScraper.name,
        source_event_id=f"{cf}-{day:%Y%m%d}",
        url=link or s.site_url,
        title=title if not place or place in title else f"{place} - {title}",
        description="\n\n".join(t for t in (clean_str(item.get("description")), price) if t) or None,
        start_at=start,
        end_at=end,
        time_known=bool(slots),
        venue_name=place,
        latitude=park.get("latitude"),
        longitude=park.get("longitude"),
        source_genres=[k.lower() for k in [*activity.get("kinds", []), s.category]],
        images=[image] if image else [],
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        tickets=tiers,
        is_sold_out=bool(slots) and not any("avail" in state for state, _ in slots),
        extra={"purchase_url": s.checkfront_url + s.booking_path.format(id=cf)},
    )
