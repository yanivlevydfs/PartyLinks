"""Planet / Rav-Hen: a branch's day of film-events to one ScrapedEvent per film."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.scrapers.planet import parse_film_events, split_attributes

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/planet"
BRANCH = json.loads((FIXTURES / "cinemas.json").read_text("utf-8"))["body"]["cinemas"][0]
DAY = json.loads((FIXTURES / "film_events_1025_2026-10-22.json").read_text("utf-8"))["body"]


def test_screenings_grouped_per_film_and_business_day() -> None:
    events = {e.source_event_id: e for e in parse_film_events(DAY, BRANCH, "planet", TZ)}
    assert set(events) == {"8328s2r:1025:2026-10-22", "7141s2r:1025:2026-10-22", "8590s2r:1025:2026-10-22"}
    loves = events["8590s2r:1025:2026-10-22"]
    assert loves.title == "האהבות שלנו" and loves.start_at is not None
    assert loves.start_at.isoformat() == "2026-10-22T14:30:00+03:00"
    # The 00:00 screening belongs to the 22nd's business day, listed last.
    assert loves.extra["showtimes"][-1] == "2026-10-23T00:00:00+03:00"
    assert loves.description == "הקרנות: 14:30, 17:00, 19:30, 22:05, 00:00"
    assert loves.min_age == 12 and loves.extra["movie_genres"] == ["drama", "israeli"]
    assert str(loves.url) == "https://www.planetcinema.co.il/films/our-loves/8590s2r"


def test_venue_and_genres() -> None:
    event = parse_film_events(DAY, BRANCH, "planet", TZ)[0]
    assert (event.venue_name, event.address, event.city) == ("פלאנט אילון", "קניון אילון - אבא הלל סילבר 301", "רמת גן")
    assert (event.latitude, event.longitude) == (32.100334, 34.826633)
    assert all(e.source_genres == ["קולנוע"] for e in parse_film_events(DAY, BRANCH, "planet", TZ))
    assert event.image_url and event.image_url.endswith(".jpg")


def test_attributes() -> None:
    assert split_attributes(["all", "2d", "drama", "first-subbed-lang-he", "subbed"]) == (
        None, ["drama"], ["2d"], ["first-subbed-lang-he", "subbed"],
    )  # fmt: skip
    assert split_attributes(["16-plus", "imax", "dubbed-lang-he"])[0] == 16


def test_prices_per_format_from_the_booking_host() -> None:
    """One ticketGroups request per event and format, on the PRES host of the booking link; the
    regular ticket only, its 5 NIS online fee apart from the 49 face value."""
    import asyncio
    import copy
    from decimal import Decimal

    from partylinks.config import HttpSettings
    from partylinks.scrapers.planet import PlanetScraper, PlanetSettings

    day = copy.deepcopy(DAY)
    day["events"][1]["attributeIds"].append("imax")  # 349107, first Fear Street screening
    groups = {
        f"https://tickets5.planetcinema.co.il/api/presentations/{i}/ticketGroups": f"ticket_groups_{f}.json"
        for i, f in ((348337, "2d"), (349107, "imax"), (349108, "2d"), (349187, "2d"))
    }
    settings = PlanetSettings(cron="0 6 * * *", site_url="https://x", api_path="/il", tenant_id="1")
    scraper = PlanetScraper(settings, HttpSettings(), TZ)
    calls: list[str] = []

    async def get_json(url: str, **kwargs: object) -> object:
        calls.append(url)
        return json.loads((FIXTURES / groups[url]).read_text("utf-8"))

    scraper.get_json = get_json  # type: ignore[method-assign]

    async def price() -> list:
        async with scraper:
            return [await scraper._priced(e) for e in parse_film_events(day, BRANCH, "planet", TZ)]

    events = {e.source_event_id: e for e in asyncio.run(price())}
    assert sorted(calls) == sorted(groups)
    loves = events["8590s2r:1025:2026-10-22"]
    assert [(t.name, t.price, t.service_fee, t.total_price) for t in loves.tickets] == [
        ("רגיל", Decimal(49), Decimal(5), Decimal(54))
    ]
    fear = events["8328s2r:1025:2026-10-22"]
    assert [(t.name, t.total_price) for t in fear.tickets] == [("רגיל IMAX", Decimal(64)), ("רגיל", Decimal(54))]
    assert (fear.min_price, fear.max_price, fear.currency) == (Decimal(49), Decimal(59), "ILS")
