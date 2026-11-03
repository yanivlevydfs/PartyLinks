"""Likes / views / watching now: counted once per visitor a day, crawlers left out, on the page and the cards."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from partylinks.db import Database, delete_events
from partylinks.popularity import models as popularity
from partylinks.popularity.models import EventViewRow
from tests.test_accounts import OURS, FakeGoogle, sign_in
from tests.test_saved import _app


def _views(db: Database) -> int:
    with db.session(write=False) as s:
        return s.scalar(select(func.count()).select_from(EventViewRow)) or 0


def test_likes_views_and_watching_now(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        first = c.get("/events/test:a").json()
        assert (first["views"], first["watching"]) == (1, 0)  # alone: below min_watching (2)
        assert c.get("/events/test:a").json()["views"] == 1  # same visitor, same day: once
        assert c.get("/events/test:a", headers={"user-agent": "Googlebot/2.1"}).json()["views"] == 1  # not a view
        other = c.get("/events/test:a", headers={"user-agent": "Mozilla/5.0 other"}).json()
        assert (other["views"], other["watching"]) == (2, 2)
        sign_in(c, fake)
        c.put("/api/me/saved/test:a", headers=OURS)
        cards = {e["id"]: e for e in c.get("/events").json()["items"]}
        assert (cards["test:a"]["likes"], cards["test:a"]["views"]) == (1, 2)
        assert (cards["test:b"]["likes"], cards["test:b"]["views"]) == (0, 0)
    with db.session() as s:  # nobody there for a while: no one is watching
        s.execute(update(EventViewRow).values(last_at=datetime.now(UTC) - timedelta(minutes=10)))
    with db.session(write=False) as s:
        assert popularity.watching(s, "test:a", app.state.settings.popularity) == 0  # type: ignore[attr-defined]


def test_views_are_pruned_and_go_with_their_event(tmp_path: Path) -> None:
    app, db = _app(tmp_path)
    cfg = app.state.settings  # type: ignore[attr-defined]
    with TestClient(app) as c:
        c.get("/events/test:a")
        c.get("/events/test:b")
    with db.session() as s:
        s.execute(update(EventViewRow).where(EventViewRow.event_id == "test:b").values(day=date(2000, 1, 1)))
    popularity.prune(db, cfg.popularity, cfg.timezone)
    assert _views(db) == 1
    with db.session() as s:
        delete_events(s, ["test:a"])
    assert _views(db) == 0
