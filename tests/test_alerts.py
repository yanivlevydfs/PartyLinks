"""Alerts ("save search"): criteria, the API, the bell, For you, and the matcher (parity with /events)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update

from partylinks.accounts.models import UserRow
from partylinks.alerts import matcher
from partylinks.alerts.criteria import Criteria
from partylinks.alerts.models import AlertHitRow, AlertRow
from partylinks.api import create_app
from partylinks.db import Database, EventRow, delete_events, utcnow
from partylinks.models import TicketTier
from tests.test_accounts import OURS, FakeGoogle, _settings, sign_in
from tests.test_storage_api import event, seed

SOON = datetime.now(UTC) + timedelta(days=2)


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match events the moment they are saved (production waits matcher.SETTLE for post-scrape steps)."""
    monkeypatch.setattr(matcher, "SETTLE", timedelta(0))


def test_criteria_keep_dates_relative() -> None:
    c = Criteria(when="weekend", genres=["techno"], freeOnly=True)
    assert c.params(date(2026, 10, 29)) == {
        "genre": ["techno"], "free_entry": True, "weekend": True, "date_to": datetime(2026, 11, 5, 23, 59, 59),
    }  # fmt: skip
    ranged = Criteria(when="2026-11-01~2026-11-03").params(date(2026, 10, 29))
    assert ranged["date_from"] == datetime(2026, 11, 1) and ranged["date_to"] == datetime(2026, 11, 3, 23, 59, 59)
    with pytest.raises(ValidationError):
        Criteria(when="tonight")
    with pytest.raises(ValidationError):
        Criteria(when="2026-11-03~2026-11-01")
    assert Criteria(genres=["a", "b"]).digest() == Criteria(genres=["b", "a"]).digest()
    assert Criteria(when="weekend").is_empty() and Criteria(q="x").is_empty() and not Criteria(q="xy").is_empty()


def _app(tmp_path: Path, events: list[Any] | None = None, **extra: Any) -> tuple[Any, Database, Any]:
    settings = _settings(tmp_path, **extra)
    db = seed(
        settings, events if events is not None else [event(k, SOON + timedelta(hours=i)) for i, k in enumerate("abc")]
    )
    return create_app(settings, start_scheduler=False), db, settings


def test_alerts_api(tmp_path: Path, fake: FakeGoogle) -> None:
    app, _, _ = _app(tmp_path, max_alerts=2)
    with TestClient(app) as c:
        assert c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS).status_code == 401
        sign_in(c, fake)
        assert c.post("/api/me/alerts", json={"criteria": {"when": "weekend"}}, headers=OURS).status_code == 422
        one = c.post("/api/me/alerts", json={"criteria": {"q": "a"}}, headers=OURS)
        assert one.status_code == 422  # one letter is no word: it would have matched every event
        bad = c.post("/api/me/alerts", json={"criteria": {"genres": ["nope"]}}, headers=OURS)
        assert bad.status_code == 422 and "genre nope" in bad.text
        made = c.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "name": "Techno"}, headers=OURS)
        assert made.status_code == 201
        alert = made.json()
        again = c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS).json()
        assert again["id"] == alert["id"]  # the same search saved twice is one alert
        c.post("/api/me/alerts", json={"criteria": {"q": "night", "freeOnly": True}}, headers=OURS)
        assert c.post("/api/me/alerts", json={"criteria": {"q": "house"}}, headers=OURS).status_code == 409
        preview = c.get(f"/api/me/alerts/{alert['id']}/preview").json()
        assert preview["total"] == c.get("/events", params={"q": "techno"}).json()["total"] == 3
        assert c.post("/api/me/alerts/preview", json={"q": "techno"}).json()["total"] == 3
        edited = c.patch(f"/api/me/alerts/{alert['id']}", json={"instant": True, "active": False}, headers=OURS).json()
        assert edited["instant"] and not edited["active"]
        assert [a["name"] for a in c.get("/api/me/alerts").json()["items"]] == ["Techno", "night"]
        assert c.delete(f"/api/me/alerts/{alert['id']}", headers=OURS).status_code == 204
        c.cookies.clear()
        sign_in(c, fake, email="other@example.com", sub="g-other")
        mine = c.get("/api/me/alerts").json()["items"]
        assert mine == [] and c.get("/api/me/alerts/999/preview").status_code == 404


VARIED = [  # (id, title, city, free, days ahead, hour)
    ("t1", "Techno warehouse rave", "Tel Aviv-Yafo", True, 1, 23),
    ("t2", "House music rooftop party", "Tel Aviv-Yafo", False, 3, 22),
    ("h1", "Jazz evening at the port", "Haifa", False, 2, 20),
    ("h2", "Techno in the bay", "Haifa", True, 9, 23),
    ("j1", "Stand up comedy night", "Jerusalem", False, 4, 21),
    ("j2", "Trance festival morning", "Jerusalem", True, 20, 10),
    ("b1", "Reggae beach party", "Eilat", False, 6, 18),
]
SEARCHES = [
    {"q": "techno"},
    {"q": "party", "freeOnly": True},
    {"q": "zzzz-nothing"},
    {"genres": ["techno"]},
    {"genres": ["techno", "house"], "when": "week"},
    {"regions": ["tel_aviv"]},
    {"regions": ["tel_aviv"], "freeOnly": True},
    {"q": "techno", "regions": ["tel_aviv"]},
    {"slots": ["night"]},
    {"freeOnly": True, "when": "month"},
    {"genres": ["jazz", "stand_up"], "slots": ["evening", "night"]},
]


def _api(c: Criteria) -> dict[str, Any]:
    """The /events query string of these criteria (what the site would ask)."""
    out: dict[str, Any] = {}
    for k, v in c.params(datetime.now(ZoneInfo("Asia/Jerusalem")).date()).items():
        out[k] = "true" if v is True else v.isoformat() if isinstance(v, datetime) else v
    return out


def test_matcher_matches_what_events_shows(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db, settings = _app(tmp_path, events=[])
    assert matcher.run(settings, db) == 0  # the first pass only sets the cursor
    with TestClient(app) as c:
        sign_in(c, fake)
        made = [c.post("/api/me/alerts", json={"criteria": s}, headers=OURS) for s in SEARCHES]
        assert all(m.status_code == 201 for m in made), [m.text for m in made if m.status_code != 201]
        ids = [m.json()["id"] for m in made]
        c.cookies.clear()
        sign_in(c, fake, email="twin@example.com", sub="g-twin")  # the same search as the first user
        twin = c.post("/api/me/alerts", json={"criteria": SEARCHES[0]}, headers=OURS).json()["id"]
        base = datetime.now(ZoneInfo("Asia/Jerusalem")).replace(minute=0, second=0, microsecond=0)
        seed(settings, [
            event(k, (base + timedelta(days=d)).replace(hour=h).astimezone(UTC), title=title, address=f"{city}, Israel",
                  tickets=[TicketTier(name="Entry", price=Decimal(0 if free else 90))])
            for k, title, city, free, d, h in VARIED
        ])  # fmt: skip
        assert matcher.run(settings, db) > 0
        assert matcher.run(settings, db) == 0  # nothing new: no second hit
        matched = 0
        for alert_id, search in zip([*ids, twin], [*SEARCHES, SEARCHES[0]], strict=True):
            with db.session() as s:
                hit = set(s.scalars(select(AlertHitRow.event_id).where(AlertHitRow.alert_id == alert_id)))
            shown = {
                e["id"] for e in c.get("/events", params=_api(Criteria(**search)) | {"page_size": 50}).json()["items"]
            }
            assert hit == shown, search
            matched += 0 < len(hit) < len(VARIED)  # a search that picks some events, not none or all
        assert matched >= 8
        bell = c.get("/api/me/bell").json()
        techno = {e["id"] for e in c.get("/events", params={"q": "techno"}).json()["items"]}
        assert bell["unseen"] == len(techno) > 0 and all(i["alert"] == "techno" for i in bell["items"])
        assert c.post("/api/me/bell/seen", headers=OURS).status_code == 204
        assert c.get("/api/me/bell").json()["unseen"] == 0
        assert {e["id"] for e in c.get("/api/me/for-you").json()["items"]} == techno
        with db.session() as s:
            delete_events(s, ["test:t1"])  # retention: matches go with their event
            gone = select(func.count()).select_from(AlertHitRow).where(AlertHitRow.event_id == "test:t1")
            assert s.scalar(gone) == 0


def test_blocked_and_paused_alerts_get_nothing(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db, settings = _app(tmp_path, events=[])
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        paused = c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS).json()["id"]
        c.patch(f"/api/me/alerts/{paused}", json={"active": False}, headers=OURS)
        c.cookies.clear()
        sign_in(c, fake, email="blocked@example.com", sub="g-blocked")
        c.post("/api/me/alerts", json={"criteria": {"q": "night"}}, headers=OURS)
    with db.session() as s:
        s.scalar(select(UserRow).where(UserRow.google_sub == "g-blocked")).blocked_at = utcnow()
    seed(settings, [event("x", SOON)])
    assert matcher.run(settings, db) == 0


def test_admin_sees_the_alert_count(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db, _ = _app(tmp_path)
    with TestClient(app) as user, TestClient(app) as admin:
        sign_in(user, fake)
        user.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        sign_in(admin, fake, email="boss@spotx.test", sub="g-admin")
        rows = {u["email"]: u for u in admin.get("/admin/api/users").json()["items"]}
        assert rows["dana@example.com"]["alerts"] == 1
        uid = rows["dana@example.com"]["id"]
        admin.delete(f"/admin/api/users/{uid}", headers=OURS)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(AlertRow)) == 0  # deleting the user deletes the alerts


def test_ended_events_leave_the_bell_and_users_can_remove_notifications(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db, settings = _app(tmp_path, events=[])
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        seed(settings, [event(k, SOON + timedelta(hours=i)) for i, k in enumerate("abc")])
        matcher.run(settings, db)
        assert c.get("/api/me/bell").json()["unseen"] == 3
        # Event a ends: gone from the count and the list at once, and the next pass deletes its row.
        with db.session() as s:
            past = datetime.now(UTC) - timedelta(days=2)
            s.execute(update(EventRow).where(EventRow.id == "test:a").values(start_at=past, end_at=past + timedelta(hours=2)))
        bell = c.get("/api/me/bell").json()
        assert bell["unseen"] == 2 and {i["event"]["id"] for i in bell["items"]} == {"test:b", "test:c"}
        assert c.get("/api/me/alerts").json()["items"][0]["unseen"] == 2
        matcher.run(settings, db)
        with db.session() as s:
            assert s.scalar(select(func.count()).select_from(AlertHitRow).where(AlertHitRow.event_id == "test:a")) == 0
        # The user removes one, then all.
        assert c.delete("/api/me/bell", params={"event_id": "test:b"}).status_code == 403  # not from our pages
        assert c.delete("/api/me/bell", params={"event_id": "test:b"}, headers=OURS).status_code == 204
        assert [i["event"]["id"] for i in c.get("/api/me/bell").json()["items"]] == ["test:c"]
        assert c.delete("/api/me/bell", headers=OURS).status_code == 204
        assert c.get("/api/me/bell").json() == {"unseen": 0, "items": []}
        matcher.run(settings, db)
        assert c.get("/api/me/bell").json()["items"] == []  # removed ones never come back


def test_cleared_notifications_leave_for_you_and_are_not_emailed(
    tmp_path: Path, fake: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    import resend

    from partylinks.mail import jobs

    sent: list[Any] = []
    monkeypatch.setattr(resend.Batch, "send", lambda params, options=None: sent.extend(params) or {"data": []})
    monkeypatch.setattr("partylinks.mail.send.PAUSE", 0)
    app, db, settings = _app(tmp_path, events=[])
    settings.mail = settings.mail.model_validate({"resend_api_key": "re_test"})
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "instant": True}, headers=OURS)
        seed(settings, [event(k, SOON + timedelta(hours=i)) for i, k in enumerate("abc")])
        matcher.run(settings, db)
        ids = lambda: [e["id"] for e in c.get("/api/me/for-you").json()["items"]]  # noqa: E731
        assert ids() == ["test:a", "test:b", "test:c"]  # now cached
        assert c.delete("/api/me/bell", params={"event_id": "test:a"}, headers=OURS).status_code == 204
        assert ids() == ["test:b", "test:c"]  # the clear dropped the cached row
        assert c.get("/api/me/bell").json()["unseen"] == 2
        assert c.delete("/api/me/bell", headers=OURS).status_code == 204
        assert ids() == [] and c.get("/api/me/bell").json() == {"unseen": 0, "items": []}
        assert c.get("/api/me/alerts").json()["items"][0]["unseen"] == 0
        with db.session() as s:  # kept, stamped: the key keeps the matcher from adding them back
            assert s.scalar(select(func.count()).select_from(AlertHitRow).where(AlertHitRow.cleared_at.is_not(None))) == 3
        matcher.run(settings, db)
        assert ids() == [] and c.get("/api/me/bell").json()["items"] == []
    jobs.instant(settings, db)
    assert sent == []  # cleared before the email went: not sent
