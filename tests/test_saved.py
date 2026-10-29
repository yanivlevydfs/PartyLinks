"""Saved events: hearts for a signed-in user, merge on sign-in, cards for signed-out hearts, clean-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from partylinks.api import create_app
from partylinks.db import Database, delete_events
from partylinks.saved.models import SavedEventRow
from tests.test_accounts import OURS, FakeGoogle, _settings, sign_in
from tests.test_storage_api import event, seed


def _app(tmp_path: Path, **extra: object) -> tuple[object, Database]:
    settings = _settings(tmp_path, **extra)
    soon = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event(k, soon + timedelta(hours=i)) for i, k in enumerate("abcd")])
    return create_app(settings, start_scheduler=False), db


def test_hearts_of_a_signed_in_user(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        assert c.put("/api/me/saved/test:a", headers=OURS).status_code == 401
        sign_in(c, fake)
        assert c.put("/api/me/saved/test:a").status_code == 403  # not from our pages
        assert c.put("/api/me/saved/test:a", headers=OURS).json()["ids"] == ["test:a"]
        assert c.put("/api/me/saved/test:a", headers=OURS).json()["ids"] == ["test:a"]  # twice is once
        assert c.put("/api/me/saved/nope", headers=OURS).status_code == 404
        c.put("/api/me/saved/test:c", headers=OURS)
        saved = c.get("/api/me/saved")
        assert saved.headers["cache-control"] == "private, no-store"
        assert [e["id"] for e in saved.json()["items"]] == ["test:a", "test:c"]  # soonest first
        assert c.delete("/api/me/saved/test:a", headers=OURS).json()["ids"] == ["test:c"]
        with db.session() as s:
            delete_events(s, ["test:c"])  # retention: the heart goes with its event
            assert s.scalar(select(func.count()).select_from(SavedEventRow)) == 0


def test_merge_on_sign_in_and_the_limit(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path, max_saved=3)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.put("/api/me/saved/test:a", headers=OURS)
        merged = c.post(
            "/api/me/saved/merge", json={"ids": ["test:a", "test:b", "gone", "test:c", "test:d"]}, headers=OURS
        )
        assert sorted(merged.json()["ids"]) == ["test:a", "test:b", "test:c"]  # unknown dropped, capped at 3
        assert c.put("/api/me/saved/test:d", headers=OURS).status_code == 409
        uid = c.get("/api/me").json()["id"]
        c.delete("/api/me", headers=OURS)  # deleting the account deletes the hearts
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(SavedEventRow).where(SavedEventRow.user_id == uid)) == 0


def test_cards_for_signed_out_hearts(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        got = c.get("/events/cards", params=[("ids", "test:b"), ("ids", "test:a"), ("ids", "nope")]).json()["items"]
        assert [e["id"] for e in got] == ["test:a", "test:b"]
        assert c.get("/events/cards", params=[("ids", str(i)) for i in range(101)]).status_code == 422


def test_admin_sees_the_saved_count(tmp_path: Path, fake: FakeGoogle) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as user, TestClient(app) as admin:
        sign_in(user, fake)
        user.put("/api/me/saved/test:a", headers=OURS)
        sign_in(admin, fake, email="boss@spotx.test", sub="g-admin")
        rows = {u["email"]: u for u in admin.get("/admin/api/users").json()["items"]}
        assert rows["dana@example.com"]["saved"] == 1 and rows["boss@spotx.test"]["saved"] == 0
