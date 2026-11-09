"""Nature and Parks Authority: board item + Checkfront date page to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.parks import ParksSettings, _month, open_dates, parse_activity, parse_event, parse_park

TZ = "Asia/Jerusalem"
FIX = Path(__file__).parent / "fixtures/parks"
ITEM = json.loads((FIX / "board_item.json").read_text("utf-8"))["635"]
S = ParksSettings(cron="13 10,22 * * *")
ACTIVITY = parse_activity({
    "link": "https://www.parks.org.il/activity/nahaltaninim-2/",
    "acf": {"checkfront_id": "635", "rpSelect": 1889, "activity_type_home": {"label": "סיור מודרך"},
            "act_important": {"act_important_age": ["לכל המשפחה"]}},
})  # fmt: skip


def test_park_coordinates_from_the_israeli_grid() -> None:
    park = parse_park({"id": 1889, "acf": {"lat_lng": "192371", "long_lng": "716948"}}, S.grid_epsg)
    assert park == {"id": 1889, "latitude": 32.546409, "longitude": 34.916084}  # Nahal Taninim
    assert parse_park({"id": 1, "acf": {"lat_lng": "", "long_lng": ""}}, S.grid_epsg) is None


def test_open_dates_and_months() -> None:
    days = open_dates(json.loads((FIX / "calendar.json").read_text("utf-8")))
    assert days == [date(2026, 12, 5), date(2026, 12, 11), date(2026, 12, 12), date(2026, 12, 19)]
    assert [_month(date(2026, 11, 9), n) for n in range(3)] == [date(2026, 11, 9), date(2026, 12, 1), date(2027, 1, 1)]


def test_event_from_slots_with_park_and_booking() -> None:
    assert ACTIVITY is not None
    parks = {1889: {"id": 1889, "latitude": 32.55, "longitude": 34.92}}
    page = (FIX / "date.html").read_text("utf-8")
    e = parse_event(ITEM, date(2026, 12, 5), page, {"635": ACTIVITY}, parks, S, TZ)
    assert e is not None and e.source_event_id == "635-20261205"
    assert e.title == "שמורת טבע נחל תנינים - סיור סתווי ומיוחד" and e.venue_name == "שמורת טבע נחל תנינים"
    assert e.start_at == datetime(2026, 12, 5, 9, 0, tzinfo=ZoneInfo(TZ)) and e.time_known
    assert e.end_at == datetime(2026, 12, 5, 15, 30, tzinfo=ZoneInfo(TZ))  # last slot 14:00 + 90 minutes
    assert (e.latitude, e.longitude) == (32.55, 34.92) and str(
        e.url
    ) == "https://www.parks.org.il/activity/nahaltaninim-2/"
    assert e.extra["purchase_url"] == "https://checkfrontcom.checkfront.com/reserve/?item_id=635&lang_id=he"
    assert e.images[0].startswith("https://static.parks.org.il/wp-content/uploads/")  # not the imgp.php thumbnail
    assert e.source_genres == ["סיור מודרך", "לכל המשפחה", "טבע"] and not e.is_sold_out
    settings = Settings()
    genres = Enricher(settings.enrichment, settings.timezone).enrich(e).genres  # type: ignore[union-attr]
    assert {"tours", "kids"} <= set(genres)


def test_a_date_without_slots_has_no_time() -> None:
    e = parse_event(ITEM, date(2026, 12, 5), "<html></html>", {}, {}, S, TZ)
    assert e is not None and not e.time_known and e.latitude is None
