"""Tel Aviv-Yafo Jazz Festival parser test on the real home page, schedule and press texts (November 2026)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers import tlvjazzfest as jazz

FIXTURES = Path(__file__).parent / "fixtures" / "tlvjazzfest"
TZ = "Asia/Jerusalem"
CFG = jazz.TlvJazzFestSettings.model_validate(Settings().sources["tlvjazzfest"])


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _events() -> list:
    festival = jazz.parse_festival(_read("home.html"))
    slots = jazz.js_value(_read("data.js"), "SCHEDULE")
    info = jazz.js_value(_read("artists-info.js"), "ARTIST_INFO")
    return jazz.build_events(festival, slots, info, CFG, TZ)


def test_festival_and_scripts() -> None:
    festival = jazz.parse_festival(_read("home.html"))
    assert (festival["start"], festival["venue"], festival["street"]) == (date(2026, 11, 19), "בית רומנו", "דרך יפו 9")
    info = jazz.js_value(_read("artists-info.js"), "ARTIST_INFO")  # a JavaScript object: unquoted keys, comments
    assert info["Johnny O'Neal"]["photo"] == "assets/artists/johnny-oneal.jpg"
    assert len(jazz.js_value(_read("data.js"), "SCHEDULE")) == 84
    with pytest.raises(ParseError):
        jazz.js_value("var x = 1;", "SCHEDULE")


def test_a_day_event_and_a_headliner() -> None:
    events = {e.source_event_id: e for e in _events()}
    assert len(events) == 8  # three days, five headliners
    thursday = events["2026-11-19"]
    assert thursday.title == "פסטיבל הג'אז תל אביב-יפו · יום חמישי 19.11"
    assert thursday.start_at == datetime(2026, 11, 19, 18, 0, tzinfo=ZoneInfo(TZ))
    assert thursday.end_at == datetime(2026, 11, 20, 1, 0, tzinfo=ZoneInfo(TZ))  # 1500 minutes: 01:00 the next night
    assert "ג׳וני או׳ניל" in thursday.artists and "JOHNNY O'NEAL" not in thursday.artists  # the bio's Hebrew name
    assert not any("AFTER PARTY" in a or "ג׳אם" in a for a in thursday.artists)
    assert thursday.description and "20:45–21:45 · רחבה · ג׳וני או׳ניל (הופעה מרכזית)" in thursday.description
    assert (thursday.venue_name, thursday.address, thursday.city) == ("בית רומנו", "דרך יפו 9", "תל אביב-יפו")
    assert [(t.price, t.is_free) for t in thursday.tickets] == [(0, True)]
    johnny = events["thu-1245-רחבה"]
    assert johnny.title == "ג׳וני או׳ניל · פסטיבל הג'אז תל אביב-יפו" and johnny.artists == ["ג׳וני או׳ניל"]
    assert johnny.start_at == datetime(2026, 11, 19, 20, 45, tzinfo=ZoneInfo(TZ))
    assert johnny.images == ["https://tlvjazzfest26.com/assets/artists/johnny-oneal.jpg"]
    assert johnny.description and johnny.description.startswith("ג׳וני אוניל")
    enriched = Enricher(Settings().enrichment, TZ).enrich(johnny)
    assert enriched is not None and {"jazz", "festival"} <= set(enriched.genres)
