"""Mega Events: RSC event objects and price rebuild on a trimmed real home page (no network)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers.megaevents import MegaeventsScraper, MegaeventsSettings

PAGE = Path(__file__).parent / "fixtures" / "megaevents" / "home.html"
SETTINGS = MegaeventsSettings(
    cron="0 * * * *", site_url="https://www.mega-events.co.il", event_page_path="/order/{event_id}"
)


def _scrape() -> dict[str, ScrapedEvent]:
    scraper = MegaeventsScraper(SETTINGS, HttpSettings(), "Asia/Jerusalem")
    page = PAGE.read_text(encoding="utf-8")

    async def fake_get_text(url: str, **_: object) -> str:
        assert url == "https://www.mega-events.co.il/"
        return page

    async def collect() -> dict[str, ScrapedEvent]:
        return {e.source_event_id: e async for e in scraper.scrape()}

    scraper.get_text = fake_get_text  # type: ignore[method-assign]
    return asyncio.run(collect())


def test_scrape_home_payload(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        events = _scrape()
    assert set(events) == {"1197", "921", "1115"}  # 1227 is a football match
    assert not caplog.records  # rebuilt prices equal the home page's JSON-LD prices

    stevie = events["1197"]
    assert str(stevie.url) == "https://www.mega-events.co.il/order/1197"
    assert stevie.title == "סטיבי וונדר" and stevie.artists == ["Stevie Wonder"]
    assert stevie.start_at == datetime(2026, 10, 26, tzinfo=ZoneInfo("Europe/Berlin"))
    assert (stevie.city, stevie.country) == ("קלן", "DE")
    # Ticket only: cheapest ticket 472 + ticket_only_markup 200.
    assert (stevie.currency, stevie.min_price) == ("USD", Decimal(672))
    assert "package" not in stevie.extra

    # Package: 246 + markup 100 + flight 550 + hotel 210 + package_fee 175.
    assert events["921"].min_price == Decimal(1281)
    assert events["921"].extra["package"] is True

    harry = events["1115"]
    assert harry.artists == ["Harry Styles"]  # "Harry Styles London": the city is dropped
    assert harry.country == "GB" and harry.start_at is not None and harry.start_at.tzinfo == ZoneInfo("Europe/London")
    assert harry.min_price == Decimal(1695) and not harry.is_sold_out


def test_slim_listing_rows_use_the_sites_from_price() -> None:
    """Nov 2026 home page: rows carry from_price / sold_out instead of tickets and markups."""
    from partylinks.scrapers.megaevents import event_objects, parse_event

    row = (
        '{"id":869,"name":"מיוז","name_english":"Muse","type":"tx_event","date":"2026-11-21",'
        '"location":{"name":"מילאנו, איטליה","latitude":45.46,"longitude":9.19,"city_iata":"MIL",'
        '"country_code":""},"package_mode":"package","card_image_url":"","art_image_url":"https://x/a.png",'
        '"from_price":1234,"sold_out":false}'
    )
    match = row.replace('"id":869', '"id":870').replace('"name_english":"Muse"', '"name_english":"A vs B"')
    raws = event_objects(f"[{row},{match}]")
    assert [r["id"] for r in raws] == [869, 870]
    event = parse_event(raws[0], "https://www.mega-events.co.il/order/{event_id}", "Asia/Jerusalem", Decimal(175))
    assert event is not None
    assert (event.min_price, event.is_sold_out, event.city, event.country) == (Decimal(1234), False, "מילאנו", "IT")
    assert [str(u) for u in event.images] == ["https://x/a.png"]
    assert parse_event(raws[1], "https://x/{event_id}", "Asia/Jerusalem", Decimal(175)) is None  # football


def test_genre_pages_tag_their_events() -> None:
    """Each /c/music/genres/<slug> page linked from the home page adds its slug to its events."""
    scraper = MegaeventsScraper(SETTINGS, HttpSettings(), "Asia/Jerusalem")
    home = PAGE.read_text(encoding="utf-8").replace(
        "</body>", '<a href="/c/music/genres/classic-rock">x</a><a href="/c/music/genres/pop">y</a></body>'
    )
    seen: list[str] = []

    async def fake_get_text(url: str, **_: object) -> str:
        seen.append(url)
        return "" if url.endswith("/pop") else home  # classic-rock: the home page's events

    async def collect() -> dict[str, ScrapedEvent]:
        return {e.source_event_id: e async for e in scraper.scrape()}

    scraper.get_text = fake_get_text  # type: ignore[method-assign]
    events = asyncio.run(collect())
    assert seen[1:] == [
        "https://www.mega-events.co.il/c/music/genres/classic-rock",
        "https://www.mega-events.co.il/c/music/genres/pop",
    ]
    assert all(e.source_genres == ["concert", "classic rock"] for e in events.values())
