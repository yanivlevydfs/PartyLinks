"""Cinema City: the PRES screenings feed to one ScrapedEvent per (movie, branch, business day)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.cinemacity import Branch, CinemaCitySettings, parse_presentations

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/cinemacity"
LISTING = json.loads((FIXTURES / "presentations.json").read_text("utf-8"))
FEATURES = json.loads((FIXTURES / "features.json").read_text("utf-8"))
SETTINGS = CinemaCitySettings(
    cron="47 6,18 * * *", api_url="https://tickets.cinema-city.co.il", presentations_path="/api/presentations/",
    features_path="/api/features/", movie_url="https://www.cinema-city.co.il/movie/{id}",
    branches={"1350": Branch(address='צה"ל 35', city="חדרה")},
)  # fmt: skip


def _events() -> dict[str, ScrapedEvent]:
    return {e.source_event_id: e for e in parse_presentations(LISTING, FEATURES, SETTINGS, TZ, date(2026, 10, 22))}


def test_one_event_per_movie_branch_day_within_days_ahead() -> None:
    events = _events()
    # 3 (movie, branch) pairs x today + 2 days; the 25th is beyond days_ahead=3.
    assert len(events) == 9 and not any(k.endswith("2026-10-25") for k in events)
    e = events["6462:1350:2026-10-22"]
    assert e.title == "קלייפייס" and e.start_at is not None
    assert e.start_at.isoformat() == "2026-10-22T11:50:00+03:00"
    # The midnight show belongs to the business day before.
    assert e.extra["showtimes"][-1] == "2026-10-23T00:00:00+03:00"
    assert e.description is not None and e.description.endswith("הקרנות: 11:50, 14:25, 17:00, 19:00, 21:30, 00:00")
    assert (e.venue_name, e.address, e.city) == ("סינמה סיטי חדרה", 'צה"ל 35', "חדרה")
    assert e.source_genres == ["קולנוע"] and e.extra["movie_genres"]
    assert str(e.url) == "https://www.cinema-city.co.il/movie/6462"
    assert e.image_url and e.image_url.startswith("https://cdn.presglobal.pro/")


def test_min_age_from_rating_and_missing_features() -> None:
    rated = parse_presentations(LISTING, [], SETTINGS, TZ, date(2026, 10, 22))
    adult = next(e for e in rated if e.source_event_id == "6433:1173:2026-10-23")
    assert adult.min_age == 16 and adult.image_url is None and adult.address is None
    assert adult.description == "הקרנות: 22:20, 00:30"


def test_scrape_prices_per_event_and_hall_type(monkeypatch) -> None:
    """ticketGroups once per event and hall type; the regular ticket at face value plus the online fee."""
    import asyncio
    from datetime import datetime
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from partylinks.config import HttpSettings
    from partylinks.scrapers import cinemacity

    api = SETTINGS.api_url
    scraper = cinemacity.CinemaCityScraper(SETTINGS, HttpSettings(), TZ)
    calls: list[str] = []

    async def get_json(url: str, **kwargs: object) -> object:
        calls.append(url)
        if url == api + SETTINGS.presentations_path:
            return LISTING
        if url == api + SETTINGS.features_path:
            return FEATURES
        name = "vip" if "/887259/" in url else "reg"
        return json.loads((FIXTURES / f"ticket_groups_{name}.json").read_text("utf-8"))

    scraper.get_json = get_json  # type: ignore[method-assign]
    morning = datetime(2026, 10, 22, 8, tzinfo=ZoneInfo(TZ))
    monkeypatch.setattr(cinemacity, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: morning)}))

    async def collect() -> list[ScrapedEvent]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(collect())}
    assert len(events) == 9 and not scraper.failures
    assert len([c for c in calls if c.endswith("/ticketGroups")]) == 10  # 9 events, one with a VIP hall too
    glilot = events["6462:1170:2026-10-22"]
    assert [(t.name, t.price, t.service_fee, t.total_price) for t in glilot.tickets] == [
        ("רגיל", Decimal("49.9"), Decimal("4.9"), Decimal("54.8")),
        ("VIP", Decimal(159), Decimal("4.9"), Decimal("163.9")),
    ]
    assert (glilot.min_price, glilot.max_price) == (Decimal("49.9"), Decimal(159))
    assert all(e.tickets and e.min_price == Decimal("49.9") for e in events.values())
