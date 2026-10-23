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
