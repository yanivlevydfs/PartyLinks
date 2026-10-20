"""Radio EPGB: Supabase rows -> events, and credential discovery from the page (no network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from partylinks.config import HttpSettings
from partylinks.scrapers.epgb import EpgbScraper, EpgbSettings, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "epgb"
SETTINGS = EpgbSettings(
    cron="0 * * * *",
    site_url="https://epgb.co.il",
    page_path="/",
    event_page_path="/event.html?id={id}",
    events_path="/rest/v1/events",
    supabase_url="https://fallback.supabase.co",
    venue_name="Radio EPGB",
    address="שד״ל 7, תל אביב",
    city="תל אביב",
)


def _rows() -> list[dict]:
    return json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))


def test_parse_event() -> None:
    own, external, _ = (parse_event(r, SETTINGS, "Asia/Jerusalem") for r in _rows())

    assert own.source == "epgb"
    assert own.source_event_id == "19cb5551-ccd4-4496-817a-809114fa1539"
    assert own.title == "DANCEHALL STATION"
    assert own.start_at.isoformat() == "2026-10-20T22:30:00+03:00"
    assert own.artists == ["I-TEK", "EDDIE BOMBO"]
    assert own.source_genres == ["AFROBETS DANCEHALL"]
    assert own.min_age == 24
    assert str(own.url) == "https://epgb.co.il/event.html?id=19cb5551-ccd4-4496-817a-809114fa1539"
    assert own.image_url and own.image_url.endswith("1792332344666.png")
    assert (own.venue_name, own.address, own.city) == ("Radio EPGB", "שד״ל 7, תל אביב", "תל אביב")
    assert own.description and own.description.startswith("דיג'ייז מעולים")

    # An external ticket link replaces the club's own event page.
    assert str(external.url).startswith("https://chillz.com/electric-dreams-alternative")
    assert external.artists == ["IDAN GAVRIEL"] and external.source_genres == []


def test_lineup_prefers_artist_lineup() -> None:
    row = {**_rows()[0], "artist_lineup": [{"name": "A"}, "B"], "show_description": False}
    event = parse_event(row, SETTINGS, "Asia/Jerusalem")
    assert event.artists == ["A", "B"] and event.description is None


def test_credentials_from_page_with_fallback() -> None:
    scraper = EpgbScraper(SETTINGS.model_copy(update={"anon_key": "eyJcfg.eyJcfg.cfg"}), HttpSettings(), "Asia/Jerusalem")
    pages = iter([
        "const SUPABASE_URL  = 'https://abc123.supabase.co';\nconst SUPABASE_ANON_KEY = 'eyJhb.eyJpc3.sig_-1';",
        "<html>no key here</html>",
    ])

    async def fake_get_text(url: str, **_: object) -> str:
        assert url == "https://epgb.co.il/"
        return next(pages)

    scraper.get_text = fake_get_text  # type: ignore[method-assign]
    assert asyncio.run(scraper._credentials()) == ("https://abc123.supabase.co", "eyJhb.eyJpc3.sig_-1")
    assert asyncio.run(scraper._credentials()) == ("https://fallback.supabase.co", "eyJcfg.eyJcfg.cfg")
