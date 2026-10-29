"""The /events filter builder is a plain function the alert matcher can call without a request."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from partylinks.api import build_event_filters, create_app
from partylinks.db import EventRow
from tests.test_storage_api import event, seed, settings_for


def test_builder_matches_the_endpoint(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    db = seed(settings, [event(k, datetime.now(UTC) + timedelta(days=2, hours=i)) for i, k in enumerate("abc")])
    with db.session(write=False) as session:
        count = lambda **params: session.scalar(  # noqa: E731
            select(func.count()).select_from(EventRow).where(*build_event_filters(settings, **params))
        )
        free, techno = count(free_entry=True), count(q="techno")
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/events", params={"free_entry": "true"}).json()["total"] == free == 3
        assert c.get("/events", params={"q": "techno"}).json()["total"] == techno == 3
        assert c.get("/events", params={"free_entry": "false"}).json()["total"] == 0
