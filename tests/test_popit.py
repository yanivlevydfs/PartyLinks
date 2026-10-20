"""Popit parser test against a trimmed real /explore/all page (3 events, RSC payload only)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import popit

PAGE = Path(__file__).parent / "fixtures" / "popit" / "explore_all.html"
TZ = "Asia/Jerusalem"
URL = "https://popit.events/event/{event_id}"


def test_popit_listing_payload_events() -> None:
    rsc = popit.flight_payload(PAGE.read_text(encoding="utf-8"))
    events = {e.source_event_id: e for e in (popit.parse_event(o, rsc, URL, TZ) for o in popit.event_objects(rsc))}
    assert set(events) == {"2230", "2251", "2248"}

    spirit = events["2230"]
    assert str(spirit.url) == "https://popit.events/event/2230"
    assert spirit.start_at == datetime(2026, 10, 26, 22, 0, tzinfo=ZoneInfo(TZ))
    assert spirit.end_at == datetime(2026, 10, 27, 4, 0, tzinfo=ZoneInfo(TZ))
    assert spirit.city == "תל אביב-יפו" and spirit.address == "בן אביגדור 17, תל אביב-יפו"
    assert (spirit.latitude, spirit.longitude) == (32.0670944, 34.7906813)
    assert spirit.min_age == 15 and spirit.organizer == "SPIRIT_IL"
    assert spirit.description and spirit.description.startswith("SPIRIT PRESENT")  # "$ref" text row resolved
    assert spirit.extra["purchase_url"].startswith("https://zygo.co.il/event/")
    assert spirit.image_url and spirit.source_genres[0] == "Mainstream (Israeli)"

    gagarin = events["2248"]
    assert gagarin.venue_name == "גגרין | Gagarin Tel Aviv"
    assert gagarin.artists == ["Asher Swissa", "Rising Dust", "Zarka"]

    # Approximate (secret) location: no coordinates, venue or street address.
    secret = events["2251"]
    assert secret.latitude is None and secret.venue_name is None and secret.address is None
    assert secret.city == "כינרת" and secret.min_age == 21
