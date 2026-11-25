"""Culture Universe (cu-cultureuniverse.co.il): event ids from the lists, each event page's performances.

Fixtures (25.11.2026, <style> blocks dropped, the accessibility widget's site key blanked): Carmen (one
performance, Haifa), Fiddler on the Roof (four performances in four towns), a past show's page (no date) and
the dance & ballet category page.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import SCRAPERS
from partylinks.scrapers.cultureuniverse import (
    CultureUniverseScraper,
    CultureUniverseSettings,
    category_name,
    event_ids,
    parse_event_page,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "cultureuniverse"
CFG = CultureUniverseSettings.model_validate({"cron": "0 0 * * *"})


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_registered() -> None:
    assert SCRAPERS["cultureuniverse"] is CultureUniverseScraper
    assert "cultureuniverse" in Settings().sources


def test_category_page_ids_and_name() -> None:
    page = _page("category-22.html")
    ids = event_ids(page)
    assert len(ids) == 10 and 4619 in ids
    assert category_name(page) == "הופעות מחול ובלט"


def test_one_performance() -> None:
    events = parse_event_page(_page("carmen.html"), "https://x/event/4619", 4619, ["הופעות מחול ובלט"], CFG, TZ)
    assert len(events) == 1
    e = events[0]
    assert (e.source, e.source_event_id) == ("cultureuniverse", "19354")  # the schedule id
    assert e.title == "כרמן - הבלט הישראלי"
    assert e.start_at == datetime(2027, 2, 25, 20, 0, tzinfo=ZoneInfo(TZ))
    assert (e.venue_name, e.city) == ("תיאטרון הצפון חיפה", "חיפה")
    assert e.address == "תיאטרון הצפון חיפה, חיפה"
    assert e.extra["purchase_url"] == "https://kupatbravo.co.il/announce/buy/209907"
    assert e.min_price == Decimal(145)
    assert e.images == ["https://bravo.israelinfo.co.il/show/image/360x248/85852.jpg"]
    assert e.source_genres == ["הופעות מחול ובלט"]
    assert str(e.url).startswith("https://www.cu-cultureuniverse.co.il/event/4619")
    assert e.description and "אורלסקי" in e.description and "על המופע" not in e.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(e)
    assert enriched is not None and enriched.genres


def test_every_performance_with_its_hall() -> None:
    events = parse_event_page(_page("fiddler.html"), "https://x/event/4800", 4800, [], CFG, TZ)
    assert [e.source_event_id for e in events] == ["16965", "16966", "16967", "19439"]
    assert [e.city for e in events] == ["אור עקיבא", "כפר סבא", "אשדוד", "פתח תקווה"]
    last = events[-1]
    assert last.start_at == datetime(2027, 4, 24, 21, 0, tzinfo=ZoneInfo(TZ))  # summer time (+03:00)
    assert last.end_at == datetime(2027, 4, 24, 23, 45, tzinfo=ZoneInfo(TZ))
    assert last.extra["purchase_url"] == "https://kupatbravo.co.il/announce/buy/213653"
    assert last.source_genres == ["מחזמר"]  # the page's own category
    enriched = Enricher(Settings().enrichment, TZ).enrich(last)
    assert enriched is not None and enriched.genres


def test_past_show_has_no_performance() -> None:
    assert parse_event_page(_page("past.html"), "https://x/event/4436", 4436, [], CFG, TZ) == []
