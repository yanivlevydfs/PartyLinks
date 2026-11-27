"""A home row's order (home_rails.rows[].sort): "latest" lists the latest to end first ("open latest tonight")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.home_rails.config import HomeRail
from tests.test_storage_api import event, seed, settings_for


def test_latest_lists_the_latest_end_first_and_trending_stays_the_default(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [
        HomeRail(key="late", genre=["techno"], sort="latest"),
        HomeRail(key="usual", genre=["techno"]),
    ]
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    seed(settings, [
        event("early", start, end_at=start + timedelta(hours=2), source_genres=["techno"]),
        event("dawn", start + timedelta(hours=1), end_at=start + timedelta(hours=9), source_genres=["techno"]),
        # No end time: the start plus retention.assumed_duration_hours (3 h): between the other two.
        event("open", start + timedelta(hours=3), end_at=None, source_genres=["techno"]),
    ])  # fmt: skip
    assert HomeRail(key="x").sort == "trending"
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = client.get("/events/rails").json()["rails"]
    assert [e["id"] for e in rails["late"]] == ["test:dawn", "test:open", "test:early"]
    assert [e["id"] for e in rails["usual"]] == ["test:early", "test:dawn", "test:open"]  # by start, no other sites
