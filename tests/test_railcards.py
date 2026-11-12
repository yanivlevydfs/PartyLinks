"""A home row shows a show once, its nearest date; the lists keep every date (partylinks.railcards)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.home_rails.config import HomeRail
from tests.test_storage_api import event, seed, settings_for


def test_a_row_shows_a_show_once_at_its_nearest_date(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("later", now + timedelta(days=4), title="The Tour"),
        event("near", now + timedelta(days=2), title="The Tour "),  # the same title, whatever the spacing
        event("other", now + timedelta(days=3), title="Something Else"),
    ])  # fmt: skip
    with TestClient(create_app(settings)) as client:
        rows = client.get("/events/rails").json()["rails"]
        new_today = {e["id"] for e in rows["new_today"]}
        assert new_today == {"test:near", "test:other"}
        assert client.get("/events").json()["total"] == 3  # the list keeps every date


def test_the_hitech_row_has_tech_conferences_not_every_conference(tmp_path: Path) -> None:
    """2026-11-13: a couples', a trauma and a Harry Potter fans' conference filled the hi-tech row."""
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("couples", now + timedelta(days=2), title="כנס זוגי", source_genres=["conference"]),
        event("cyber", now + timedelta(days=2), title="כנס קהילת Cyber Israel", source_genres=["conference", "cyber"]),
        event("ai", now + timedelta(days=3), title="מבוא לבינה מלאכותית", source_genres=["ai"]),
    ])  # fmt: skip
    with TestClient(create_app(settings)) as client:
        hitech = {e["id"] for e in client.get("/events/rails").json()["rails"].get("hitech", [])}
        culture = {e["id"] for e in client.get("/events", params={"genre": "g_culture"}).json()["items"]}
    assert hitech == {"test:cyber", "test:ai"}  # the couples' conference is not hi-tech
    assert "test:couples" in culture  # it is culture


def test_the_home_rows_come_from_settings(tmp_path: Path) -> None:
    """home_rails in settings (the admin's config tab): a row's genres and the row list are settings, not code."""
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [HomeRail(key="hitech", genre=["ai"])]
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("cyber", now + timedelta(days=2), title="כנס Cyber", source_genres=["cyber"]),
        event("ai", now + timedelta(days=3), title="מבוא לבינה מלאכותית", source_genres=["ai"]),
    ])  # fmt: skip
    with TestClient(create_app(settings)) as client:
        rails = client.get("/events/rails").json()["rails"]
    assert {e["id"] for e in rails["hitech"]} == {"test:ai"}
    assert "electronic" not in rails and "trending" not in rails
