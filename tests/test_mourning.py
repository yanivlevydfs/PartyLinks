"""Days of mourning (partylinks.mourning): their dates, their hours, and no party of those hours in promotion."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.home_rails.config import HomeRail
from partylinks.mourning import core
from tests.test_storage_api import event, seed, settings_for

IL = ZoneInfo("Asia/Jerusalem")
# Hebcal's Israel calendar (hebcal.com/hebcal?v=1&cfg=json&maj=on&min=on&mod=on&i=on&year=...), checked 28.11.2026.
HEBCAL = {
    2026: {"yom_hashoah": "2026-04-14", "memorial_day": "2026-04-21", "tisha_bav": "2026-07-23", "yom_kippur": "2026-09-21"},
    2027: {"yom_hashoah": "2027-05-04", "memorial_day": "2027-05-11", "tisha_bav": "2027-08-12", "yom_kippur": "2027-10-11"},
    2028: {"yom_hashoah": "2028-04-24", "memorial_day": "2028-05-01", "tisha_bav": "2028-08-01", "yom_kippur": "2028-09-30"},
    2029: {"yom_hashoah": "2029-04-12", "memorial_day": "2029-04-18", "tisha_bav": "2029-07-22", "yom_kippur": "2029-09-19"},
    2030: {"yom_hashoah": "2030-04-30", "memorial_day": "2030-05-07", "tisha_bav": "2030-08-08", "yom_kippur": "2030-10-07"},
}  # fmt: skip


@pytest.mark.parametrize("year", sorted(HEBCAL))
def test_dates_match_hebcal(year: int) -> None:
    assert {key: rule(year).isoformat() for key, rule in core.RULES.items()} == HEBCAL[year]


def test_moved_days() -> None:
    assert core.yom_hashoah(2028) == date(2028, 4, 24)  # 27 Nisan a Sunday: Monday 28
    assert core.yom_hashoah(2029) == date(2029, 4, 12)  # 27 Nisan a Friday: Thursday 26
    assert core.tisha_bav(2029) == date(2029, 7, 22)  # 9 Av a Shabbat: Sunday 10 Av


def test_windows_from_the_eve_and_memorial_day_ends_at_independence_day() -> None:
    cfg = Settings()
    s = cfg.mourning
    by_key = {w.key: w for w in core.windows(cfg, 2027)}
    assert set(by_key) == set(s.days)
    assert by_key["yom_hashoah"].start == datetime(2027, 5, 3, s.eve_hour, tzinfo=IL)
    memorial = by_key["memorial_day"]
    assert memorial.start == datetime(2027, 5, 10, s.eve_hour, tzinfo=IL)
    assert memorial.end == datetime(2027, 5, 11, tzinfo=IL) + timedelta(hours=s.days["memorial_day"].end_hour)


def test_current_only_while_on_or_ahead_hours_before() -> None:
    cfg = Settings()
    cfg.mourning.ahead_hours = 0
    eve = datetime(2027, 10, 10, cfg.mourning.eve_hour, tzinfo=IL)  # Yom Kippur's eve
    assert core.current(cfg, eve - timedelta(hours=1)) == []
    assert [w.key for w in core.current(cfg, eve)] == ["yom_kippur"]
    assert core.tag(cfg, eve) and not core.tag(cfg, eve - timedelta(hours=1))  # the rows rebuild as it begins
    cfg.mourning.ahead_hours = 2
    assert [w.key for w in core.current(cfg, eve - timedelta(hours=1))] == ["yom_kippur"]
    cfg.mourning.enabled = False
    assert core.clauses(cfg, eve) == []


def _events(start: datetime) -> list:  # type: ignore[type-arg]
    after = start + timedelta(days=3)  # past the window
    return [
        event("party_in", start, title="Rave", source_genres=["techno"]),
        event("club_in", start + timedelta(hours=2), title="Club", source_genres=["nightclubs"]),
        event("lecture_in", start, title="Lecture on history", source_genres=["lectures"]),
        event("party_after", after, title="Rave later", source_genres=["techno"]),
    ]


def test_a_party_in_the_hours_is_left_out_a_lecture_and_a_later_party_stay(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    eve = datetime(2027, 5, 3, settings.mourning.eve_hour, tzinfo=IL)  # Yom HaShoah 2027's eve
    db = seed(settings, _events(eve + timedelta(hours=3)))
    where = core.clauses(settings, eve + timedelta(minutes=5))
    assert where
    with db.session() as session:
        kept = set(session.scalars(select(EventRow.source_event_id).where(*where)))
    assert kept == {"lecture_in", "party_after"}


def test_home_rows_leave_out_parties_in_the_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [HomeRail(key="parties", genre=["g_parties"]), HomeRail(key="everything")]
    now = datetime.now(UTC).replace(microsecond=0)
    window = core.Window("yom_hashoah", now - timedelta(hours=1), now + timedelta(days=1))
    monkeypatch.setattr(core, "windows", lambda cfg, year: [window])
    seed(settings, _events(now + timedelta(hours=3)))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = client.get("/events/rails").json()["rails"]
    assert [e["id"] for e in rails["parties"]] == ["test:party_after"]
    assert {e["id"] for e in rails["everything"]} == {"test:lecture_in", "test:party_after"}
    # Listings stay complete.
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        listed = {e["id"] for e in client.get("/events").json()["items"]}
    assert {"test:party_in", "test:club_in", "test:lecture_in", "test:party_after"} <= listed
