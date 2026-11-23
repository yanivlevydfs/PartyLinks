"""Yitzhak Rabin Center (rabincenter.org.il): one wp-json call to the ``event`` post type, dates from the text.

Fixture (23.11.2026): six posts as wp-json sends them with ``_embed`` (only the fields read): two shows with
their date in the text (30.11 and 28.12.2026), an artist meeting whose date is only in its artwork, a past
memorial evening, a past exhibition and a staff summer-festival offer (August 2026).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.rabincenter import RabinCenterScraper, RabinCenterSettings, parse_post, ticket_link

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "rabincenter"
POSTS: list[dict[str, Any]] = json.loads((FIXTURES / "events.json").read_text("utf-8"))
BY_ID = {p["id"]: p for p in POSTS}
CFG = RabinCenterSettings.model_validate(
    {
        "cron": "0 0 * * *",
        "venue_name": "מרכז יצחק רבין",
        "address": "חיים לבנון 8",
        "city": "תל אביב-יפו",
        "latitude": 32.102909,
        "longitude": 34.7997091,
        "labels": {"תרבות ישראלית": ["מופע"]},
    }
)
BOX_OFFICE = "https://web.attractinet.co.il/RabinCenter/?jumpTo=Purches&lang=he"


def _event(post_id: int) -> ScrapedEvent:
    event = parse_post(BY_ID[post_id], CFG, TZ)
    assert event is not None
    return event


def test_show_date_time_price_and_box_office() -> None:
    show = _event(15392)
    assert (show.source, show.source_event_id) == ("rabincenter", "15392")
    assert show.title == "כשבנאי פגש את אלוני"
    # "30.11.2026 / יום שני / 11:30"
    assert show.start_at == datetime(2026, 11, 30, 11, 30, tzinfo=IL) and show.time_known
    assert (show.venue_name, show.city, show.latitude) == ("מרכז יצחק רבין", "תל אביב-יפו", 32.102909)
    assert show.min_price == Decimal(60)
    # The site pastes "להזמנות:" after the link: cut off.
    assert show.extra["purchase_url"] == BOX_OFFICE
    assert show.images and show.images[0].startswith("https://www.rabincenter.org.il/wp-content/uploads/")
    assert show.source_genres == ["תרבות ישראלית", "מופע"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and "live_show" in enriched.genres
    assert "tours" not in enriched.genres  # "כולל סיור מודרך במוזיאון" is the show's add-on


def test_second_show() -> None:
    assert _event(14739).start_at == datetime(2026, 12, 28, 12, 0, tzinfo=IL)


def test_no_date_in_the_text_no_event() -> None:
    assert parse_post(BY_ID[15639], CFG, TZ) is None  # Sasson Gabai: the date is only in the artwork


def test_exhibition_is_no_event() -> None:
    assert parse_post(BY_ID[12750], CFG, TZ) is None


def test_past_post_and_box_office_link_only() -> None:
    old = _event(15217)  # the summer festival's first full date: past
    assert old.start_at is not None and old.start_at < datetime(2026, 11, 1, tzinfo=IL)
    assert ticket_link('<a href="https://www.facebook.com/rabin">f</a>', CFG.ticket_hosts) is None
    encoded = '<a href="https://web.attractinet.co.il/RabinCenter/?jumpTo=Purches&amp;lang=he%D7%9C%D7%94:">x</a>'
    assert ticket_link(encoded, CFG.ticket_hosts) == BOX_OFFICE


def test_run_keeps_coming_events_nearest_first() -> None:
    scraper = build_scraper("rabincenter", Settings())
    assert isinstance(scraper, RabinCenterScraper)
    scraper.http.request_delay_seconds = 0

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wp-json/wp/v2/event"
        return httpx.Response(200, json=POSTS, headers={"X-WP-Total": str(len(POSTS))})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert not scraper.failures
    ids = [e.source_event_id for e in events]
    assert "15537" not in ids and "15217" not in ids  # past
    assert [i for i in ids if i in {"15392", "14739"}] == [i for i in ["15392", "14739"] if i in ids]
