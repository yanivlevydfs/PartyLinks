"""AGOL parser tests against the real sign-up page (7 November 2026: AGOL Takeover, night of 6-7.11)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import zone
from partylinks.scrapers import agol

PAGE = (Path(__file__).parent / "fixtures" / "agol" / "page.html").read_text(encoding="utf-8")
TZ = "Asia/Jerusalem"
URL = "https://agol-join.vercel.app/"
S = agol.AgolSettings(cron="0 0 * * *", city="תל אביב-יפו", latitude=32.0506, longitude=34.7661)


def test_next_event_is_the_night_after_the_date_written() -> None:
    event = agol.parse_page(PAGE, URL, S, TZ)
    assert event is not None
    assert event.title == "AGOL Takeover"
    # "Fri 6.11.2026 · 00:00–06:00" is the night of 6-7.11 (the flyer says so).
    assert event.start_at == datetime(2026, 11, 7, 0, 0, tzinfo=zone(TZ))
    assert event.end_at == datetime(2026, 11, 7, 6, 0, tzinfo=zone(TZ))
    assert event.source_event_id == "2026-11-07"
    assert (event.venue_name, event.address) == ("ארט קלאב", "הזרם 9")
    assert (event.latitude, event.longitude) == (32.0506, 34.7661)
    assert event.lineup == ["Yoni Yarchi", "Shigaluv", "Glenn Holmes"]
    assert event.images == ["https://agol-join.vercel.app/brand/takeover-2026-11-06-1080.jpg"]
    assert not (event.extra or {}).get("purchase_url")  # a guest list, and no Instagram / WhatsApp link


def test_house_sub_genres_are_house_only() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    event = agol.parse_page(PAGE, URL, S, TZ)
    # organic house is no chill: house and its own sub-genres only
    assert event is not None and enricher._detect_genres(event) == ["house", "organic_house", "progressive_house"]
    for declared, genres in [
        (["chill house"], ["house"]),
        (["צ׳יל האוס"], ["house"]),  # geresh as Hebrew writes it
        (["chill"], ["downtempo_chill"]),
        (["organic"], ["downtempo_chill"]),  # a declared category alone
        (["afro house"], ["afro_house", "house"]),
    ]:
        assert enricher._detect_genres(event.model_copy(update={"source_genres": declared})) == genres


def test_evening_start_stays_on_its_day_and_no_event_block_is_none() -> None:
    evening = PAGE.replace("00:00–06:00", "22:00–04:00")
    event = agol.parse_page(evening, URL, S, TZ)
    assert event is not None
    assert event.start_at == datetime(2026, 11, 6, 22, 0, tzinfo=zone(TZ))
    assert event.end_at == datetime(2026, 11, 7, 4, 0, tzinfo=zone(TZ))
    assert agol.parse_page("<html><body><p>soon</p></body></html>", URL, S, TZ) is None
