"""Pinned events lead 🔥 Trending and every row they match, never one they don't (partylinks.home_pins)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_storage_api import event, seed, settings_for


def test_a_pinned_event_leads_trending_and_the_rows_it_matches(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("pin", now + timedelta(days=5), title="Techno Nature", source_genres=["techno"]),
        *(event(f"t{i}", now + timedelta(days=2), title=f"Techno {i}", source_genres=["techno"]) for i in range(4)),
        event("talk", now + timedelta(days=2), title="A lecture", source_genres=["lectures"]),
    ])  # fmt: skip
    settings.home_pins.events = ["test:pin"]
    with TestClient(create_app(settings)) as client:
        rows = client.get("/events/rails").json()["rails"]
    assert rows["trending"][0]["id"] == "test:pin" and rows["trending"][0]["pinned"] is True
    assert rows["electronic"][0]["id"] == "test:pin"
    assert all(e["id"] != "test:pin" for e in rows.get("lectures", []))  # not a row it doesn't match
    assert sum(e["id"] == "test:pin" for e in rows["electronic"]) == 1  # once, not twice
