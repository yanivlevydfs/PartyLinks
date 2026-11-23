"""Tzavta (tzavta.co.il): category pages, the board, each event page's ``model`` script, Smarticket buy pages.

Fixtures (23.11.2026), trimmed real pages: the Short Theatre Festival's category page (``/category/17``), six
rows of the board ``/shows``, the ``model`` objects of nine event pages (the festival's office and bar stages,
a guest show, the festival's umbrella page without dates, a road show at Kibbutz Yagur sold elsewhere, a
"Sold Out!" date, a show Smarticket's API does not list, a date the box office cancelled, an artist's own box
office) and the Smarticket ``/api/shows`` entries of their box-office codes (texts dropped).
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
import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.retention import is_current
from partylinks.scrapers import build_scraper
from partylinks.scrapers import tzavta as module
from partylinks.scrapers.smarticket import parse_shows_body
from partylinks.scrapers.tzavta import (
    TzavtaScraper,
    TzavtaSettings,
    declared_genres,
    event_ids,
    offers_index,
    page_model,
    parse_category,
    parse_page,
    redirect_path,
    ticket_link,
)

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "tzavta"
MODELS: dict[str, dict[str, Any]] = json.loads((FIXTURES / "events.json").read_text("utf-8"))
SHOWS_BODY = (FIXTURES / "smarticket_shows.json").read_text("utf-8")
OFFERS = offers_index(parse_shows_body(SHOWS_BODY, "tzavta.smarticket.co.il"))
CATEGORY = (FIXTURES / "category_17.html").read_text("utf-8")
BOARD = (FIXTURES / "shows.html").read_text("utf-8")
FESTIVAL = "פסטיבל תיאטרון קצר #28"
CFG = Settings().sources["tzavta"]
S = CFG if isinstance(CFG, TzavtaSettings) else TzavtaSettings.model_validate(CFG)
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=IL)


def _page(event_id: str, categories: list[str] | None = None) -> list[ScrapedEvent]:
    return parse_page(MODELS[event_id], categories if categories is not None else [FESTIVAL], OFFERS, S, TZ)


def test_category_page_name_and_cards() -> None:
    name, ids = parse_category(CATEGORY)
    assert name == FESTIVAL  # the double space collapsed
    assert ids[:4] == ["5257", "5260", "5258", "5259"] and len(ids) == 12
    assert parse_category("<html><body><h1>404</h1></body></html>") == (None, [])


def test_board_and_umbrella_links() -> None:
    assert {"5252", "5260", "5205", "3446"} <= set(event_ids(BOARD))
    umbrella = MODELS["5257"]
    linked = event_ids(f"{umbrella['Decription']} {umbrella['Editor']}")
    assert {"5258", "5259", "5260", "5261", "5252", "5025"} <= set(linked)
    assert _page("5257") == []  # the festival's own page has no date: no event


def test_page_model_reads_the_script() -> None:
    html = f"<script>\n  const model = {json.dumps(MODELS['5252'], ensure_ascii=False)};\n  if (x) {{}}\n</script>"
    model = page_model(html)
    assert model is not None and model["Id"] == 5252 and model["MainShow"]["Show_Place"] == "צוותא 2"
    assert page_model("<html>no model</html>") is None


def test_festival_office_stage_every_date_with_its_buy_page() -> None:
    events = _page("5260")
    assert [e.start_at for e in events][:3] == [
        datetime(2026, 12, 24, 18, 0, tzinfo=IL),
        datetime(2026, 12, 24, 20, 0, tzinfo=IL),
        datetime(2026, 12, 25, 12, 0, tzinfo=IL),
    ]
    assert len(events) == 6  # every performance, not only the first
    first = events[0]
    assert (first.source, first.source_event_id) == ("tzavta", "5260:9096")
    assert str(first.url) == "https://www.tzavta.co.il/event/5260"
    assert first.title == "פסטיבל תיאטרון קצר / קצר במשרד"
    assert (first.venue_name, first.address, first.city) == ("צוותא", "אבן גבירול 30", "תל אביב")
    assert (first.latitude, first.longitude) == (S.latitude, S.longitude)
    assert first.extra["hall"] == "משרד"
    # The Smarticket performance page, as the smarticket source lists it: dedup merges the two.
    assert first.extra["purchase_url"] == "https://tzavta.smarticket.co.il/event/18388"
    assert first.min_price == Decimal(65)
    assert first.images[0].startswith("https://www.tzavta.co.il/download/events/Event_Image_")
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None and {"festival", "theatre"} <= set(enriched.genres)


def test_bar_stage_and_guest_show() -> None:
    bar = _page("5261")[0]
    assert (bar.venue_name, bar.extra["hall"]) == ("צוותא", "BABU בר")
    assert (bar.min_price, bar.max_price) == (Decimal(75), Decimal(95))
    assert bar.end_at == datetime(2026, 12, 24, 22, 0, tzinfo=IL)  # Smarticket's end time
    guest = _page("5252")
    assert len(guest) == 1 and guest[0].extra["purchase_url"] == "https://tzavta.smarticket.co.il/event/18403"


def test_road_show_elsewhere_and_external_box_office() -> None:
    yagur = _page("5116", ["מיוחדים"])[0]
    assert (yagur.venue_name, yagur.city) == ("תיאטרון יד למגינים", "יגור")
    assert yagur.latitude is not None and yagur.latitude > 32.7
    assert yagur.extra["purchase_url"].startswith("https://www.rev-orch.com/shows/")
    moti = _page("3511", ["סטנד אפ"])[0]
    assert moti.extra["purchase_url"] == "https://www.motystandup.com/event-details/zwwt-tl-byb-14-01-27"
    assert moti.venue_name == "צוותא"


def test_sold_out_place_and_cancelled_date() -> None:
    first, second = _page("5205", ["תיאטרון"])
    assert first.is_sold_out and first.extra["hall"] is None and first.venue_name == "צוותא"
    assert not second.is_sold_out and second.extra["hall"] == "צוותא 2"
    assert _page("81", ["תיאטרון"]) == []  # the box office: "ההצגה לא תתקיים"


def test_show_not_in_the_api_gets_the_redirect() -> None:
    event = _page("3446", ["סטנד אפ"])[0]
    assert "purchase_url" not in event.extra and event.extra["tickets_code"] == "3714"
    assert redirect_path(event, S) == "/iframe/api/redirect_event?key=3714/2026-12-31/2130"


def test_undated_item_has_no_redirect_and_no_event() -> None:
    # An item without a start (the bug of 23.11.2026: start_at.date() on None) gets no redirect path.
    undated = ScrapedEvent(
        source="tzavta", source_event_id="1:1", url="https://www.tzavta.co.il/event/1", title="x",
        extra={"tickets_code": "3714"},
    )  # fmt: skip
    assert redirect_path(undated, S) is None
    # A performance without a date is no event; one without a clock is a day, time unknown.
    model = {**MODELS["5252"], "MainShow": {**MODELS["5252"]["MainShow"], "Tickets_Date": None}}
    assert parse_page(model, [], OFFERS, S, TZ) == []
    model = {**MODELS["5252"], "MainShow": {**MODELS["5252"]["MainShow"], "Show_Time": ""}}
    (day,) = parse_page(model, [], OFFERS, S, TZ)
    assert day.start_at == datetime(2026, 12, 28, 0, 0, tzinfo=IL) and not day.time_known and day.end_at is None


def test_categories_hall_and_labels() -> None:
    assert declared_genres(["צוותא 2", "תיאטרון"], S) == ["תיאטרון"]  # the hall's listing is no genre
    assert declared_genres(["מיוחדים"], S) == ["מיוחדים", "מופע"]
    assert ticket_link('<a href="https://www.facebook.com/Tzavta">f</a><a href="/event/1">x</a>', S) is None


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    def current(start: Any, end: Any, settings: Any, timezone: str, now: Any = None, time_known: bool = True) -> bool:
        return is_current(start, end, settings, timezone, now=NOW, time_known=time_known)

    monkeypatch.setattr(module, "is_current", current)


@pytest.mark.usefixtures("fixed_now")
def test_run_reads_every_section_nearest_first() -> None:
    scraper = build_scraper("tzavta", Settings())
    assert isinstance(scraper, TzavtaScraper)
    scraper.http.max_retries = 0
    scraper._pace.delay = scraper._pace.base_delay = 0  # ~80 mocked requests, no pause between them
    asked: list[str] = []

    def page(model: dict[str, Any]) -> str:
        return f"<html><script>\n const model = {json.dumps(model, ensure_ascii=False)};\n</script></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if request.url.host == "tzavta.smarticket.co.il":
            if path == "/api/shows":
                return httpx.Response(200, text=SHOWS_BODY)
            if path == "/iframe/api/redirect_event":
                return httpx.Response(302, headers={"Location": "/iframe/show/3714"})
            return httpx.Response(200, text="<html>show</html>")
        if path == "/":
            return httpx.Response(200, text='<a href="/category/17\r\n">festival</a> <a href="/event/3446">x</a>')
        if path == "/shows":
            return httpx.Response(200, text=BOARD)
        if path == "/category/17":
            return httpx.Response(200, text=CATEGORY)
        if path.startswith("/event/") and (model := MODELS.get(path.rsplit("/", 1)[1])):
            return httpx.Response(200, text=page(model))
        return httpx.Response(404, text="<h1>404</h1>")

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert not scraper.failures  # unused category ids answer 404: skipped, not failures
    assert "/category/60" in asked and "/event/5025" in asked  # every category id; the umbrella's links
    starts = [e.start_at for e in events if e.start_at]
    assert starts == sorted(starts)
    ids = {e.source_event_id.split(":")[0] for e in events}
    assert {"5260", "5261", "5252", "5205", "3446"} <= ids and "81" not in ids and "5257" not in ids
    festival = [e for e in events if e.source_event_id.startswith(("5260:", "5261:", "5252:"))]
    assert len(festival) == 11 and all(FESTIVAL.lower() in e.source_genres for e in festival)
    rudi = next(e for e in events if e.source_event_id.startswith("3446:"))
    assert rudi.extra["purchase_url"] == "https://tzavta.smarticket.co.il/show/3714"  # the redirect's target
