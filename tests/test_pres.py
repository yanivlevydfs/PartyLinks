"""PRES platform: two hosts (Kupat on the older POST backend, Beit Lessin) on trimmed real
API payloads, run through the scraper with a fake transport (no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.config import HttpSettings
from partylinks.models import TierStatus
from partylinks.scrapers import pres

FIXTURES = Path(__file__).parent / "fixtures" / "pres"
TZ = "Asia/Jerusalem"
KUPAT = "https://tickets.kupat.co.il"
LESSIN = "https://lessin.presglobal.store"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _scrape(monkeypatch) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    settings = pres.PresSettings(
        cron="12 */3 * * *",
        presentations_path="/api/presentations/",
        features_path="/api/features/",
        ticket_groups_path="/api/presentations/{id}/ticketGroups",
        event_page_path="/order/{id}",
        skip_titles=["בדיקה"],
        hosts=[
            pres.PresHost(
                key="kupat",
                url=KUPAT,
                use_venue_location=False,
                ticket_groups_method="POST",
                image_path="/api/features/{feature_id}/media/contentDesktopImage?raw=1",
            ),
            pres.PresHost(
                key="lessin",
                url=LESSIN,
                venue="תיאטרון בית ליסין",
                city="תל אביב",
                address="שדרות שאול המלך 34, תל אביב",
            ),
        ],
    )
    scraper = pres.PresScraper(settings, HttpSettings(), TZ)
    responses = {
        ("GET", f"{KUPAT}/api/presentations/"): "kupat_presentations.json",
        ("GET", f"{KUPAT}/api/features/"): "kupat_features.json",
        (
            "POST",
            f"{KUPAT}/api/presentations/55025/ticketGroups",
        ): "kupat_ticket_groups_55025.json",
        (
            "POST",
            f"{KUPAT}/api/presentations/55632/ticketGroups",
        ): "kupat_ticket_groups_55632.json",
        ("GET", f"{LESSIN}/api/presentations/"): "lessin_presentations.json",
        ("GET", f"{LESSIN}/api/features/"): "lessin_features.json",
        (
            "GET",
            f"{LESSIN}/api/presentations/45126/ticketGroups",
        ): "lessin_ticket_groups_45126.json",
    }
    calls: list[tuple[str, str]] = []

    def fake(method: str):
        async def call(url: str, **_: object) -> Any:
            calls.append((method, url))
            return _fixture(responses[(method, url)])

        return call

    scraper.get_json = fake("GET")  # type: ignore[method-assign]
    scraper.post_json = fake("POST")  # type: ignore[method-assign]
    monkeypatch.setattr(pres, "_now", lambda tz: datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo(tz)))

    async def collect() -> list[Any]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(collect())}
    assert not scraper.failures
    return events, calls


def test_scrape_hosts(monkeypatch) -> None:
    events, calls = _scrape(monkeypatch)
    # The 2021 Lessin VOD row and Kupat's "בדיקה" test show are dropped.
    assert set(events) == {"kupat:55025", "kupat:55632", "lessin:45126", "lessin:45140"}
    # Prices are read once per show: both Lessin tour dates share one request, from the sooner one.
    assert sum(1 for _, url in calls if "ticketGroups" in url) == 3

    boaz = events["kupat:55025"]
    assert boaz.title == "בעז שרעבי והאופרה הקאמרית הישראלית"
    assert boaz.start_at == datetime(2026, 10, 20, 21, 0, tzinfo=ZoneInfo(TZ))
    assert str(boaz.url) == f"{KUPAT}/order/55025"
    assert (boaz.venue_name, boaz.city, boaz.address) == (
        'היכל התרבות ת"א',
        "תל אביב",
        None,
    )
    assert (boaz.latitude, boaz.longitude) == (32.0729117, 34.7803692)
    assert boaz.source_genres == ["מיינסטרים", "מוסיקה מזרחית"]
    assert boaz.image_url == f"{KUPAT}/api/features/2366/media/contentDesktopImage?raw=1"
    assert boaz.description and not boaz.is_sold_out
    assert boaz.tickets and all(t.status is TierStatus.ON_SALE for t in boaz.tickets)
    assert boaz.tickets[0].service_fee == Decimal(5)
    assert boaz.min_price is not None and boaz.min_price < boaz.max_price

    itay = events["kupat:55632"]
    assert itay.is_sold_out and all(t.status is TierStatus.SOLD_OUT for t in itay.tickets)
    assert itay.latitude is None  # Waze place link without coordinates

    tour, tour_sold = events["lessin:45126"], events["lessin:45140"]
    assert tour.title == "סיור מאחורי הקלעים"
    assert tour.start_at == datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo(TZ))
    assert (tour.venue_name, tour.city, tour.address) == (
        "תיאטרון בית ליסין",
        "תל אביב",
        "שדרות שאול המלך 34, תל אביב",
    )
    assert tour.extra["hall"] == "פואיה בית ליסין"
    assert tour.image_url and tour.image_url.startswith("https://cdn.presglobal.pro/lessin/feature/")
    assert [(t.name, t.price) for t in tour.tickets] == [("אולם", Decimal(50))]
    assert tour_sold.is_sold_out and tour_sold.tickets[0].price == Decimal(50)


def test_og_image_is_encoded_and_skips_wordpress_placeholder() -> None:
    from partylinks.parsing import og_image

    page = '<meta property="og:image" content="https://cdn.example/uploads/פוסטר.jpg">'
    assert og_image(page) == "https://cdn.example/uploads/%D7%A4%D7%95%D7%A1%D7%98%D7%A8.jpg"
    assert og_image('<meta property="og:image" content="https://x.example/wp-admin/admin-ajax.png">') is None
    assert og_image("<html></html>") is None
