"""Yad Vashem's online reservations: the dated public tours -> one event per slot."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.yadvashem import (
    YadVashemScraper,
    YadVashemSettings,
    code,
    months,
    open_days,
    parse_price,
    parse_slot,
    select_activities,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/yadvashem"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


# As served on 23.11.2026, trimmed to the fields read: the public's activity list, two activities' prices,
# and the Hebrew Friday tour's December template and closed days.
TOURS = _load("tours.json")
DETAILS = {"222": _load("details_222.json"), "285": _load("details_285.json")}
TIMES_DEC = _load("arrival_times_222_2026_12.json")
CLOSED_DEC = _load("close_dates_222_2026_12.json")


def _settings(**http: object) -> YadVashemSettings:
    """The config block's values (config/settings.yaml ``sources.yadvashem``)."""
    return YadVashemSettings(
        cron="0 0 * * *",
        venue_name="יד ושם",
        address="הר הזיכרון, ירושלים",
        city="ירושלים",
        latitude=31.774157,
        longitude=35.177274,
        http=dict(http),
    )


def _activity(tour_code: str) -> dict[str, Any]:
    return next(a for a in select_activities(TOURS) if code(a) == tour_code)


def test_dated_public_activities_once_per_event() -> None:
    picked = select_activities(TOURS)
    # 17 activities: the dated ones only.
    assert len(TOURS) == 17 and [code(a) for a in picked] == ["285", "222", "223", "261", "257"]
    undated = {t["basicDetails"]["tourCode"] for t in TOURS if not t["basicDetails"]["isNeedEvent"]}
    assert {"101", "104", "109"} <= undated  # private guiding, visit booking, bar / bat mitzvah: services
    # The shows tab sells 285's event under a second code too (287, the collections behind the scenes): kept once.
    twice = [*TOURS, {**TOURS[0], "basicDetails": {**TOURS[0]["basicDetails"], "tourCode": "287"}}]
    assert [code(a) for a in select_activities(twice)] == ["285", "222", "223", "261", "257"]
    package = {**TOURS[1], "basicDetails": {**TOURS[1]["basicDetails"], "isPackage": True}}
    assert select_activities([package, "odd", {}]) == []


def test_months_and_open_days() -> None:
    assert months(date(2026, 11, 23), 2) == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]
    assert months(date(2026, 12, 31), 1) == [date(2026, 12, 1), date(2027, 1, 1)]
    # The template lists every Friday at 10:00; none of them is closed in December.
    fridays = [date(2026, 12, 4), date(2026, 12, 11), date(2026, 12, 18), date(2026, 12, 25)]
    assert open_days(TIMES_DEC, CLOSED_DEC, date(2026, 12, 1)) == fridays
    assert open_days(TIMES_DEC, CLOSED_DEC, date(2026, 12, 5)) == fridays[1:]  # nothing before today
    assert open_days(TIMES_DEC, ["2026-12-11T00:00:00+02:00", *CLOSED_DEC], date(2026, 12, 1)) == [
        date(2026, 12, 4), date(2026, 12, 18), date(2026, 12, 25),
    ]  # fmt: skip
    assert open_days({"05/12/2026": ["10:00"], "bad": ["10:00"]}, [], date(2026, 12, 1)) == []  # a Saturday


def test_price_is_the_per_person_products() -> None:
    assert parse_price(DETAILS["222"], 3) == (Decimal(45), "דמי רישום לסיורים בודדים 10 ₪ + סיור מודרך לקהל הרחב 35 ₪")
    assert parse_price(DETAILS["285"], 3)[0] == Decimal(45)
    assert parse_price(None, 3) == (None, None)
    assert parse_price(DETAILS["222"], 2) == (None, None)  # none priced per group


def test_slot_event() -> None:
    s = _settings()
    event = parse_slot(_activity("222"), date(2026, 12, 4), "10:00", 34, parse_price(DETAILS["222"], 3), s, TZ)
    assert event is not None
    assert event.title == "שישי ביד ושם – סיור מודרך"
    assert event.source_event_id == "6472f290-2db8-ee11-8133-00505691117e-2026-12-04T1000"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-04T10:00:00+02:00"
    assert event.end_at is not None and event.end_at - event.start_at == timedelta(hours=2)
    url = "https://online-reservations.yadvashem.org/he/reservations/specificActivity/222"
    assert str(event.url) == url and event.extra == {"purchase_url": url}
    assert (event.venue_name, event.city, event.latitude, event.longitude) == (
        "יד ושם",
        "ירושלים",
        31.774157,
        35.177274,
    )
    assert event.source_genres == ["סיור מודרך"] and event.min_age == 14
    assert event.images == [
        "https://assets.yadvashem.org/image/upload/q_auto,w_1280/v1/reservations_site/assets/images/activities-images-he/222.jpg"
    ]
    assert (event.min_price, event.is_sold_out) == (Decimal(45), False)
    assert [(t.price, t.remaining, t.is_sold_out) for t in event.tickets] == [(Decimal(45), 34, False)]
    assert event.description and "בכל יום שישי, בשעה 10:00" in event.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "tours" in enriched.genres
    # "9:30" (no leading zero), 2.5 hours, no places left.
    full = parse_slot(_activity("285"), date(2026, 11, 27), "9:30", 0, (None, None), s, TZ)
    assert full is not None and full.start_at is not None
    assert full.start_at.isoformat() == "2026-11-27T09:30:00+02:00"
    assert full.end_at is not None and full.end_at.isoformat() == "2026-11-27T12:00:00+02:00"
    assert full.is_sold_out and full.tickets == [] and full.min_price is None
    # No category given: no declared genre, but the text still names a tour.
    synagogue = parse_slot(_activity("257"), date(2027, 5, 5), "13:30", 175, (None, None), s, TZ)
    assert synagogue is not None and synagogue.source_genres == []
    enriched = Enricher(Settings().enrichment, TZ).enrich(synagogue)
    assert enriched is not None and "tours" in enriched.genres
    assert parse_slot(_activity("222"), date(2026, 12, 4), "morning", 3, (None, None), s, TZ) is None


def test_run_yields_open_slots_nearest_first() -> None:
    scraper = YadVashemScraper(_settings(request_delay_seconds=0, concurrency=8), Settings().http, TZ)
    scraper.settings.months_ahead = 1
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint, params = request.url.path.rsplit("/", 1)[1], request.url.params
        asked.append(endpoint)
        if endpoint == "GetAllToursBasicDetails":
            assert (params["instituitionType"], params["lang"]) == ("2", "he")
            return httpx.Response(200, json=TOURS)
        if endpoint == "GetTourBasicAndAdditionalDetails":
            return httpx.Response(200, json=DETAILS.get(params["tourCode"], {}))
        if endpoint == "GetArrivalTimes":  # every Friday of the month at 10:00, as the app's template
            first = date(int(params["year"]), int(params["month"]), 1)
            days = [first + timedelta(days=i) for i in range(31) if (first + timedelta(days=i)).month == first.month]
            return httpx.Response(200, json={f"{d:%d/%m/%Y}": ["10:00"] if d.weekday() == 4 else [] for d in days})
        if endpoint == "GetCloseDates":
            return httpx.Response(200, json=[])
        if endpoint == "GetDateArrivalTime":
            assert params["activityDurationHours"] in ("2", "2.5")
            return httpx.Response(200, json={"10:00": 5})
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert not scraper.failures
    assert asked.count("GetAllToursBasicDetails") == 1 and asked.count("GetTourBasicAndAdditionalDetails") == 5
    assert asked.count("GetArrivalTimes") == asked.count("GetCloseDates") == 10  # 5 activities x 2 months
    assert asked.count("GetDateArrivalTime") == len(events) > 0  # one slot a day
    starts = [e.start_at for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first
    assert {e.start_at.weekday() for e in events if e.start_at} == {4}
    assert len({e.source_event_id for e in events}) == len(events)
    assert {e.min_price for e in events if code_of(e) == "222"} == {Decimal(45)}


def code_of(event: ScrapedEvent) -> str:
    return str(event.url).rsplit("/", 1)[1]


def test_config_block() -> None:
    s = build_scraper("yadvashem", Settings()).settings
    assert isinstance(s, YadVashemSettings)
    assert (s.venue_name, s.city, s.latitude, s.longitude) == ("יד ושם", "ירושלים", 31.774157, 35.177274)
    assert s.skip_title_words
