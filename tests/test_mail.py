"""Email (Resend mocked): welcome once, instant at most hourly, digest only for those who asked, once a
week, saved events first; unsubscribe links; switches; no address in the logs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import resend
from fastapi.testclient import TestClient
from resend.exceptions import ResendError
from sqlalchemy import select, update

from partylinks.accounts.models import UserRow
from partylinks.alerts.models import AlertHitRow
from partylinks.alerts import matcher
from partylinks.api import create_app
from partylinks.db import Database, EventRow
from partylinks.mail import jobs, tokens
from partylinks.mail.models import EmailRunRow
from tests.test_accounts import OURS, FakeGoogle, _settings, sign_in
from tests.test_storage_api import event, seed

SOON = datetime.now(UTC) + timedelta(days=2)


class Outbox:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        self.fail: ResendError | None = None

    def send(self, params: list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        self.calls.append((params, options or {}))
        return {"data": [{"id": str(i)} for i in range(len(params))]}

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [m for params, _ in self.calls for m in params]


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr(resend.Batch, "send", box.send)
    monkeypatch.setattr("partylinks.mail.send.PAUSE", 0)
    monkeypatch.setattr("partylinks.mail.render._forecasts", lambda settings, cards: {c["id"]: "☀️ 24°" for c in cards})
    return box


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match events the moment they are saved (production waits matcher.SETTLE for post-scrape steps)."""
    monkeypatch.setattr(matcher, "SETTLE", timedelta(0))


def _app(tmp_path: Path, events: list[Any] | None = None) -> tuple[Any, Database, Any]:
    settings = _settings(tmp_path)
    settings.mail = settings.mail.model_validate({"resend_api_key": "re_test"})
    settings.site.email = "contact@spotx.test"
    db = seed(
        settings, events if events is not None else [event(k, SOON + timedelta(hours=i)) for i, k in enumerate("abc")]
    )
    if events is None:  # the digest's filler is this weekend's events: true whatever weekday the tests run on
        with db.session() as s:
            s.execute(update(EventRow).values(is_weekend=True))
    return create_app(settings, start_scheduler=False), db, settings


def _age(db: Database, minutes: int = 10) -> None:
    with db.session() as s:
        s.execute(update(UserRow).values(created_at=datetime.now(UTC) - timedelta(minutes=minutes)))


def test_welcome_goes_once_with_unsubscribe(
    tmp_path: Path, fake: FakeGoogle, outbox: Outbox, caplog: pytest.LogCaptureFixture
) -> None:
    app, db, settings = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake)
    jobs.welcome(settings, db)
    assert outbox.messages == []  # not in the first two minutes
    _age(db)
    with caplog.at_level(logging.INFO):
        jobs.welcome(settings, db)
        jobs.welcome(settings, db)
    assert len(outbox.messages) == 1
    m = outbox.messages[0]
    assert m["to"] == ["dana@example.com"] and "SPOTX" in m["subject"] and 'dir="rtl"' in m["html"]
    assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "/api/email/unsubscribe?t=" in m["headers"]["List-Unsubscribe"] and m["reply_to"] == "contact@spotx.test"
    assert "dana@example.com" not in caplog.text and "Dana@Example.com" not in caplog.text  # no address in any log line
    with db.session() as s:
        assert s.scalar(select(EmailRunRow.sent).where(EmailRunRow.kind == "welcome")) == 1


def test_digest_only_for_opted_in_once_a_week_saved_first(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    app, db, settings = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake)
        jobs.digest(settings, db)
        assert outbox.messages == []  # did not ask for it
        c.patch("/api/me", json={"digest_opt_in": True, "locale": "en"}, headers=OURS)
        c.put("/api/me/saved/test:b", headers=OURS)
    jobs.digest(settings, db)
    jobs.digest(settings, db)  # the same week: not again
    assert len(outbox.messages) == 1
    html = outbox.messages[0]["html"]
    assert 'dir="ltr"' in html and "Your saved events this week" in html and "☀️ 24°" in html
    assert html.index("Techno night b") < html.index("Techno night a")  # the saved one leads
    assert outbox.calls[0][1]["idempotency_key"].startswith("digest:")


def test_instant_is_capped_hourly(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    app, db, settings = _app(tmp_path, events=[])
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "instant": True}, headers=OURS)
    seed(settings, [event("a", SOON)])
    matcher.run(settings, db)
    jobs.instant(settings, db)
    assert len(outbox.messages) == 1 and "Techno night a" in outbox.messages[0]["subject"]
    seed(settings, [event("b", SOON + timedelta(hours=1))])
    matcher.run(settings, db)
    jobs.instant(settings, db)
    assert len(outbox.messages) == 1  # within the hour: waits
    with db.session() as s:
        s.execute(update(UserRow).values(last_instant_at=datetime.now(UTC) - timedelta(hours=2)))
    jobs.instant(settings, db)
    assert len(outbox.messages) == 2 and "Techno night b" in outbox.messages[1]["subject"]


def test_instant_skips_old_matches(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    """Turning instant on later (an alert's or the admin switch) sends no backlog: old matches stay in the bell."""
    app, db, settings = _app(tmp_path, events=[])
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "instant": True}, headers=OURS)
    seed(settings, [event("a", SOON)])
    matcher.run(settings, db)
    with db.session() as s:
        s.execute(update(AlertHitRow).values(matched_at=datetime.now(UTC) - timedelta(minutes=settings.mail.instant_max_age_minutes + 1)))
    seed(settings, [event("b", SOON + timedelta(hours=1))])
    matcher.run(settings, db)
    jobs.instant(settings, db)
    assert len(outbox.messages) == 1 and "Techno night b" in outbox.messages[0]["subject"]
    assert "Techno night a" not in outbox.messages[0]["html"]


def test_unsubscribe_link(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    app, db, settings = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.patch("/api/me", json={"digest_opt_in": True}, headers=OURS)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "instant": True}, headers=OURS)
        uid = c.get("/api/me").json()["id"]
        c.cookies.clear()
        token = tokens.make(settings, uid, "all")
        bad = c.get("/api/email/unsubscribe", params={"t": token + "x"})
        assert bad.status_code == 400 and "text/html" in bad.headers["content-type"]  # a page, not JSON
        assert "הקישור לא תקין" in bad.text and "/account" in bad.text
        assert c.post("/api/email/unsubscribe", params={"t": "junk"}).status_code == 400
        assert c.get("/api/email/unsubscribe", params={"t": token}).status_code == 200  # a GET changes nothing
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_in
        assert c.post("/api/email/unsubscribe", params={"t": token}).status_code == 200  # one-click
        with db.session() as s:
            refused = s.get(UserRow, uid).digest_opt_out_at
            assert refused is not None
        assert c.post("/api/email/unsubscribe", params={"t": token}).status_code == 200  # again: the first refusal stays
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_out_at == refused
        sign_in(c, fake)
        assert not c.get("/api/me").json()["digest_opt_in"]
        assert not c.get("/api/me/alerts").json()["items"][0]["instant"]


def test_unsubscribe_links_survive_a_secret_rotation(tmp_path: Path) -> None:
    from pydantic import SecretStr

    settings = _settings(tmp_path)
    old = tokens.make(settings, "u1", "digest")  # signed with accounts.session_secret
    settings.mail.unsubscribe_secret = SecretStr("new-" + "n" * 32)
    new = tokens.make(settings, "u1", "digest")
    assert new != old and tokens.read(settings, old) == tokens.read(settings, new) == ("u1", "digest")
    # The session secret rotated too: the links signed with it still work from the old list.
    settings.mail.unsubscribe_old_secrets = [settings.accounts.session_secret]
    settings.accounts.session_secret = SecretStr("rotated-" + "r" * 32)
    assert tokens.read(settings, old) == tokens.read(settings, new) == ("u1", "digest")
    assert tokens.make(settings, "u1", "digest") == new  # still signed with unsubscribe_secret
    settings.mail.unsubscribe_old_secrets = []
    assert tokens.read(settings, old) is None


def test_opt_out_time_is_kept_and_shown_to_the_admin(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    app, db, settings = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake, email="boss@spotx.test", sub="g-admin")
        uid = c.get("/api/me").json()["id"]
        c.patch("/api/me", json={"digest_opt_in": True}, headers=OURS)
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_out_at is None
        c.patch("/api/me", json={"digest_opt_in": False}, headers=OURS)
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_out_at is not None
        assert c.get(f"/admin/api/users/{uid}").json()["digest_opt_out_at"]
        c.patch("/api/me", json={"digest_opt_in": True}, headers=OURS)
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_out_at is None and s.get(UserRow, uid).digest_opt_in_at is not None


def test_switches_account_errors_and_admin(tmp_path: Path, fake: FakeGoogle, outbox: Outbox) -> None:
    app, db, settings = _app(tmp_path)
    with TestClient(app) as admin:
        sign_in(admin, fake, email="boss@spotx.test", sub="g-admin")
        admin.patch("/api/me", json={"digest_opt_in": True}, headers=OURS)
        state = admin.get("/admin/api/email").json()
        assert state["ready"] and state["switches"] == {"welcome": True, "instant": True, "digest": True}
        uid = admin.get("/api/me").json()["id"]
        preview = admin.get("/admin/api/email/preview", params={"user_id": uid})
        assert preview.status_code == 200 and "Techno night a" in preview.text
        assert admin.post("/admin/api/email/test", params={"kind": "welcome"}, headers=OURS).json() == {"sent": True}
        admin.post("/admin/api/email/switches", json={"digest": False}, headers=OURS)
        jobs.digest(settings, db)
        assert len(outbox.messages) == 1  # only the test
        admin.post("/admin/api/email/switches", json={"digest": True}, headers=OURS)
        outbox.fail = ResendError(code=402, error_type="payment_required", message="unpaid", suggested_action="")
        jobs.digest(settings, db)  # an account problem: recorded, nothing raised
        runs = admin.get("/admin/api/email").json()["runs"]
        assert runs[0]["kind"] == "digest" and runs[0]["failed"] == 1 and "unpaid" in runs[0]["error"]
