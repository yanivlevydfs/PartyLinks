"""Yad Vashem's online reservations (online-reservations.yadvashem.org, "יד ושם") - the dated public tours
booked on the visitors' site, Mount of Remembrance, Jerusalem.

An Angular app over a JSON API (``api_url``; no robots.txt on either host). ``GetAllToursBasicDetails``
with ``instituitionType=2`` (``visitor_type``: individuals and families) lists what the public can book (17
activities, Nov 2026). Only those with a scheduled event (``isNeedEvent`` and an ``eventReservationId``) are
dated public tours: the Friday guided tours of the Holocaust History Museum in Hebrew, English and Russian,
the "Living Memory" exhibition tour with the Schapell Collections Center behind the scenes (the shows tab
sells that event under a second code, 287: an event is kept once) and the synagogue tours around Holocaust
Remembrance Day. The rest (private and group
guiding, audio guides, bar / bat mitzvah tours, school activities) are booked for any day: services, not
events. The lectures, ceremonies and film mornings Yad Vashem sells on Smarticket (the `smarticket` tenant
``yadvashem``) are not here; ``ogo`` lists the English Friday tour too.

Dates: per activity and month, ``GetArrivalTimes`` gives the slot template (start times per weekday, past
days included) and ``GetCloseDates`` the days closed from today on. For each open day from today on,
``GetDateArrivalTime`` (what the app reads when a day is picked) gives the slots and the places left in each
(0: sold out). The calendar is an open template, so the run reads ``months_ahead`` months (the app's own
range is eight), the nearest month first. Price: ``GetTourBasicAndAdditionalDetails``' required per-person
products (registration + tour). End: the start plus the stated duration. Artwork: the app's own picture
per activity code on Yad Vashem's Cloudinary (its gallery is not read). One venue (the meeting point is
the Visitors Center), so its place comes from the config. ~250 small JSON requests a run.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings, gather_or_cancel

_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")  # "04/12/2026"
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")  # "9:30", "10:00"
_AGE = re.compile(r"(\d{1,2})\s*\+")  # "14+"


class YadVashemSettings(SourceSettings):
    site_url: str = "https://online-reservations.yadvashem.org"
    api_url: str = "https://online-reservations-server.yadvashem.org/api/onlineReservation"
    lang: str = "he"
    # GetAllToursBasicDetails' instituitionType: 2 = individuals and families (1 = school groups).
    visitor_type: int = 2
    activity_path: str = "/{lang}/reservations/specificActivity/{code}"
    # The activity's picture (the page's og:image), at 1280 px wide. Its gallery ("<code>_1.jpg"...,
    # severalPhotosLinkedToTour) is not read: the count runs ahead of the files (285_3.jpg: 404, Nov 2026).
    image_url: str = (
        "https://assets.yadvashem.org/image/upload/q_auto,w_1280/v1/reservations_site/assets/images/"
        "activities-images-{lang}/{name}.jpg"
    )
    months_ahead: int = Field(8, ge=0)
    # The app's ActivityCategory (tourCategory) -> the category name.
    categories: dict[int, str] = Field(
        default_factory=lambda: {1: "סיור מודרך", 2: "ביקור במוזיאון", 3: "מופע אורקולי", 4: "תיאטרון", 5: "אירוע"}
    )
    # A required product's pricingMethod that is paid per participant (1: once per booking, 2: per group).
    per_person_pricing: int = 3
    tier_name: str = "כרטיס למשתתף"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class YadVashemScraper(BaseScraper[YadVashemSettings]):
    name = "yadvashem"
    settings_model = YadVashemSettings

    def _api(self, endpoint: str) -> str:
        return f"{self.settings.api_url}/{endpoint}"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tours = await self.get_json(
            self._api("GetAllToursBasicDetails"),
            params={"instituitionType": s.visitor_type, "lang": s.lang},
            expect=list,
        )
        activities = select_activities(tours)
        self.log.debug("%d dated activities of %d", len(activities), len(tours))
        details = await gather_or_cancel(
            self.optional(
                f"details {code(a)}",
                self.get_json(
                    self._api("GetTourBasicAndAdditionalDetails"), params={"tourCode": code(a), "lang": s.lang}
                ),
            )
            for a in activities
        )
        prices = {code(a): parse_price(d, s.per_person_pricing) for a, d in zip(activities, details, strict=True)}
        today = datetime.now(zone(self.timezone)).date()
        for month in months(today, s.months_ahead):  # nearest month first
            found = await gather_or_cancel(self._month(a, month, today, prices[code(a)]) for a in activities)
            events = [e for batch in found for e in batch]
            for event in sorted(events, key=lambda e: e.start_at or datetime.min):
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event

    async def _month(
        self, activity: dict[str, Any], month: date, today: date, price: tuple[Decimal | None, str | None]
    ) -> list[ScrapedEvent]:
        """One activity's events in one month; a failing month is recorded and skipped alone."""
        basic = activity["basicDetails"]
        what = f"{code(activity)} {month:%Y-%m}"
        try:
            times, closed = await gather_or_cancel([
                self.get_json(
                    self._api("GetArrivalTimes"),
                    params={"month": month.month, "year": month.year, "activityDurationHours": hours(basic), "tourTypeId": basic["id"]},
                    expect=dict,
                ),
                self.get_json(
                    self._api("GetCloseDates"), params={"tourCode": code(activity), "month": month.month, "year": month.year}, expect=list
                ),
            ])  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - one month of one activity never drops the others
            self.record_failure(what, exc)
            return []
        days = open_days(times, closed, today)
        slots = await gather_or_cancel(self._slots(activity, day) for day in days)
        events = []
        for day, day_slots in zip(days, slots, strict=True):
            for clock, places in (day_slots or {}).items():
                try:
                    event = parse_slot(activity, day, clock, places, price, self.settings, self.timezone)
                except Exception as exc:  # noqa: BLE001 - one odd slot never drops the others
                    self.record_failure(f"{what} {day} {clock}", exc)
                    continue
                if event:
                    events.append(event)
        return events

    async def _slots(self, activity: dict[str, Any], day: date) -> dict[str, Any] | None:
        basic = activity["basicDetails"]
        params = {
            "date": f"{day:%d/%m/%Y}",
            "tourTypeId": basic["id"],
            "tourCode": code(activity),
            "activityDurationHours": hours(basic),
        }
        return await self.optional(
            f"slots {code(activity)} {day}", self.get_json(self._api("GetDateArrivalTime"), params=params, expect=dict)
        )


def code(activity: dict[str, Any]) -> str:
    return str(activity["basicDetails"]["tourCode"])


def hours(basic: dict[str, Any]) -> str:
    """The duration as the app sends it: ``2``, ``2.5``."""
    return f"{to_float(basic.get('durationActivity')) or 0:g}"


def select_activities(tours: list[Any]) -> list[dict[str, Any]]:
    """The listing -> its dated public activities (a scheduled event, not a package), one per event."""
    seen: set[str] = set()
    picked = []
    for tour in tours:
        basic = (tour.get("basicDetails") if isinstance(tour, dict) else None) or {}
        event_id = tour.get("eventReservationId") if isinstance(tour, dict) else None
        dated = basic.get("isNeedEvent") and not basic.get("isPackage")
        if not (dated and event_id and basic.get("tourCode") and basic.get("id")):
            continue
        if event_id in seen:  # one event sold under two codes (285 and 287: "Living Memory" + Schapell)
            continue
        seen.add(event_id)
        picked.append(tour)
    return picked


def months(today: date, ahead: int) -> list[date]:
    """The first day of this month and of the ``ahead`` months after it."""
    return [date(today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1, 1) for i in range(ahead + 1)]


def open_days(times: dict[str, Any], closed: list[Any], today: date) -> list[date]:
    """GetArrivalTimes' template + GetCloseDates -> the days from today on that have a slot and are open."""
    shut = {c[:10] for c in closed if isinstance(c, str)}  # "2026-12-05T00:00:00" (some with "+02:00")
    days = []
    for key, clocks in times.items():
        match = _DAY.match(key)
        if not match or not clocks:
            continue
        day = date(int(match[3]), int(match[2]), int(match[1]))
        # Saturdays are closed in the app's calendar too.
        if day >= today and day.isoformat() not in shut and day.weekday() != 5:
            days.append(day)
    return sorted(days)


def parse_price(details: Any, per_person: int) -> tuple[Decimal | None, str | None]:
    """GetTourBasicAndAdditionalDetails -> the per-person price (its required products summed) and its parts."""
    extra = (details.get("additionalTourDetails") if isinstance(details, dict) else None) or {}
    parts: list[tuple[str, Decimal]] = []
    for product in extra.get("reqiredProductList") or []:
        if to_int(product.get("pricingMethod")) != per_person:
            continue
        options = product.get("priceListItemOptions") or []
        price = to_decimal(options[0].get("price")) if options else to_decimal(product.get("price"))
        if price is not None:
            parts.append((clean_str(product.get("name")) or "", price))
    if not parts:
        return None, None
    total = sum((p for _, p in parts), Decimal(0))
    return total, " + ".join(f"{name} {price.normalize():f} ₪" for name, price in parts)


def parse_slot(
    activity: dict[str, Any],
    day: date,
    clock: str,
    places: Any,
    price: tuple[Decimal | None, str | None],
    s: YadVashemSettings,
    timezone: str,
) -> ScrapedEvent | None:
    """One slot of one day -> its event; None without a time or a name."""
    basic = activity["basicDetails"]
    match = _CLOCK.match(clock.strip())
    title = clean_str(basic.get("name"))
    if not match or not title:
        return None
    start_at = datetime(day.year, day.month, day.day, int(match[1]), int(match[2]), tzinfo=zone(timezone))
    duration = to_float(basic.get("durationActivity"))
    left = to_int(places)
    tour = code(activity)
    url = s.site_url + s.activity_path.format(lang=s.lang, code=tour)
    category = s.categories.get(to_int(basic.get("tourCategory")) or 0)
    age = _AGE.search(basic.get("age") or "")
    amount, parts = price
    return ScrapedEvent(
        source=YadVashemScraper.name,
        source_event_id=f"{activity['eventReservationId']}-{start_at:%Y-%m-%dT%H%M}",
        url=url,
        title=title,
        description=html_to_text(basic.get("description")),
        start_at=start_at,
        end_at=start_at + timedelta(hours=duration) if duration else None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[category] if category else [],
        min_age=int(age.group(1)) if age else None,
        images=[s.image_url.format(lang=s.lang, name=quote(tour))],
        min_price=amount,
        max_price=amount,
        is_sold_out=left == 0,
        tickets=[TicketTier(name=s.tier_name, description=parts, price=amount, remaining=left, is_sold_out=left == 0)]
        if amount is not None
        else [],
        extra={"purchase_url": url},
    )
