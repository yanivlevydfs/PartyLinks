"""OneCity municipal sites (GeoDirectory REST API): event rows to ScrapedEvent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from partylinks.scrapers.onecity import OneCitySite, build_event, parse_rows

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/onecity"
KARMIEL = OneCitySite(url="https://www.karmiel.muni.il", city="כרמיאל")
EVEN_YEHUDA = OneCitySite(url="https://www.even-yehuda.muni.il", city="אבן יהודה")
SKIP = frozenset({"even-yehuda.smarticket.co.il"})


def _rows(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def test_karmiel_free_event_with_categories_and_place() -> None:
    events = {e.source_event_id: e for e in parse_rows(_rows("karmiel_events.json"), KARMIEL, TZ, SKIP)}
    assert list(events) == ["karmiel:17669", "karmiel:17629", "karmiel:16989"]  # recurring 16989 kept once
    e = events["karmiel:17669"]
    assert e.title == 'פסטיבל סלסה ובצאטה קהילתי במתנ"ס רבין' and e.source_genres == ["כללי", "חינמי"]
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-23T11:00:00+03:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-23T16:00:00+03:00"
    assert (e.venue_name, e.city, e.organizer) == ('מתנ"ס רבין', "כרמיאל", "רשת המתנסים כרמיאל")
    assert e.tickets[0].is_free and e.extra == {}  # a Facebook page is not a seller
    assert e.image_url and e.image_url.endswith(".jpeg") and e.description and "סלסה" in e.description
    assert (events["karmiel:17629"].venue_name, events["karmiel:17629"].address) == (
        "בית האומנים",
        "רח' משעול נורית 12",
    )
    assert events["karmiel:16989"].start_at.isoformat().startswith("2026-11-25")  # type: ignore[union-attr]


def test_even_yehuda_address_fields_links_and_course_end() -> None:
    course, music = _rows("even_yehuda_events.json")
    e = build_event(course, EVEN_YEHUDA, TZ, SKIP)
    assert e is not None and e.extra == {"purchase_url": "https://shortlink.win/1DqVc"}
    assert e.end_at is None  # Oct 6 - Dec 29 is a course, not one long evening
    m = build_event(music, EVEN_YEHUDA, TZ, SKIP)
    assert m is not None and (m.venue_name, m.address, m.city) == (None, "המתמיד 4", "אבן יהודה")
    assert m.extra == {} and m.source_genres == ["גיל שלישי"]  # a WhatsApp link is not a seller


def test_skips_covered_smarticket_and_sample_posts() -> None:
    course = _rows("even_yehuda_events.json")[0] | {
        "gd_event_buyticket": "https://even-yehuda.smarticket.co.il/event/12"
    }
    assert build_event(course, EVEN_YEHUDA, TZ, SKIP) is None
    tiberias = OneCitySite(url="https://www.tiberias.muni.il", city="טבריה")
    assert parse_rows(_rows("tiberias_sample.json"), tiberias, TZ, SKIP) == []  # the theme's undated sample
