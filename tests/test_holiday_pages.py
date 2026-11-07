"""Israeli holiday pages (partylinks.holiday_pages): dates from the search's calendar, a page once a holiday has
enough events, the /holidays index, the JSON list and the sitemap."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.holiday_pages.core import upcoming
from tests.test_storage_api import event, seed, settings_for


def test_dates_eve_to_last_day() -> None:
    found = {h.key: h for h in upcoming(Settings(), date(2026, 11, 6))}
    hanukkah = found["hanukkah"]
    assert (hanukkah.he, hanukkah.en) == ("חנוכה", "Hanukkah")
    assert hanukkah.start == date(2026, 12, 4) and hanukkah.end == date(2026, 12, 12)  # eve of 5.12 to the 8th day
    assert found["new_year"].start == date(2026, 12, 28)
    assert [h.start for h in found.values()] == sorted(h.start for h in found.values())  # soonest first


def test_a_holiday_page_lists_its_events(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    h = next(x for x in upcoming(settings, datetime.now(tz).date()) if x.key == "hanukkah")
    night = datetime.combine(h.start + timedelta(days=1), time(21), tz)
    seed(settings, [event(str(i), night + timedelta(hours=i), title=f"מסיבת חנוכה {i}") for i in range(3)]
         + [event("after", datetime.combine(h.end + timedelta(days=3), time(21), tz), title="After the holiday")])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/holiday/hanukkah")
        assert page.status_code == 200 and "מסיבת חנוכה 0" in page.text and "After the holiday" not in page.text
        assert 'href="/holiday/hanukkah"' in client.get("/holidays").text
        listed = {x["key"]: x for x in client.get("/holidays/list").json()["holidays"]}
        assert listed["hanukkah"]["events"] == 3 and listed["hanukkah"]["page"] is True
        assert listed["purim"]["page"] is False
        assert client.get("/holiday/purim").status_code == 410  # no events yet: no thin page
        assert "/holiday/hanukkah" in client.get("/sitemap-1.xml").text
