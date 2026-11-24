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
    settings.security.page_rate_limit_per_minute = settings.security.page_rate_limit_per_day = (
        100_000  # partylinks.page_guard
    )
    db = seed(settings, [event(k, datetime.now(UTC) + timedelta(days=2, hours=i)) for i, k in enumerate("abc")])
    with db.session(write=False) as session:
        count = lambda **params: session.scalar(
            select(func.count()).select_from(EventRow).where(*build_event_filters(settings, **params))
        )
        free, techno = count(free_entry=True), count(q="techno")
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/events", params={"free_entry": "true"}).json()["total"] == free == 3
        assert c.get("/events", params={"q": "techno"}).json()["total"] == techno == 3
        assert c.get("/events", params={"free_entry": "false"}).json()["total"] == 0


def test_undated_offers_are_found_by_search_only(tmp_path: Path) -> None:
    """retention.undated_sources: a course with no round set is found by a search, never in date-ordered lists;
    another source's undated row (an old page with no date) stays hidden everywhere."""
    settings = settings_for(tmp_path)
    settings.retention.undated_sources = ["fournature"]
    course = event(
        "course",
        datetime.now(UTC),
        source="fournature",
        title="Techno course for organisers",
        start_at=None,
        end_at=None,
    )
    stale = event(
        "old", datetime.now(UTC), source="eventbuzz", title="Techno festival 2022", start_at=None, end_at=None
    )
    db = seed(settings, [course, stale, event("night", datetime.now(UTC) + timedelta(days=2))])
    with db.session(write=False) as session:
        titles = lambda **params: set(
            session.scalars(select(EventRow.title).where(*build_event_filters(settings, **params)))
        )
        assert "Techno course for organisers" in titles(q="techno")
        assert "Techno festival 2022" not in titles(q="techno")
        assert titles() == {"Techno night night"}  # no search: dated events only
    settings.retention.undated_sources = []
    with db.session(write=False) as session:
        assert "Techno course for organisers" not in set(
            session.scalars(select(EventRow.title).where(*build_event_filters(settings, q="techno")))
        )
