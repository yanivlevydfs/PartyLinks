"""A home row shows a show once, its nearest date; the lists keep every date (partylinks.railcards)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
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
