"""Optional Google accounts: sign-in (Google mocked), sessions, /api/me, admin through Google, admin users."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from partylinks.accounts import google
from partylinks.accounts.config import AccountsSettings
from partylinks.accounts.models import SessionRow, UserRow
from partylinks.accounts.redact import Redact, scrub
from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, utcnow
from tests.test_storage_api import settings_for

OURS = {"X-Requested-With": "spotx"}
ADMIN = "boss@spotx.test"


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    settings.site.base_url = "http://testserver"
    settings.accounts = AccountsSettings.model_validate({
        "enabled": True, "google_client_id": "id", "google_client_secret": "secret",
        "session_secret": "s" * 40, "admin_emails": [ADMIN], **extra,
    })  # fmt: skip
    settings.admin.token = None
    return settings


class FakeGoogle:
    """Google's side of the redirect: who signs in next."""

    def __init__(self) -> None:
        self.info: dict[str, Any] = {}

    async def authorize_redirect(self, request: Any, redirect_uri: str) -> RedirectResponse:
        return RedirectResponse("https://accounts.google.com/o/oauth2/auth?fake=1", 302)

    async def authorize_access_token(self, request: Any) -> dict[str, Any]:
        return {"userinfo": self.info}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    g = FakeGoogle()
    monkeypatch.setattr(google, "client", lambda request: g)
    return g


def sign_in(
    c: TestClient, fake: FakeGoogle, email: str = "Dana@Example.com", sub: str = "g-1", nxt: str = "/events"
) -> Any:
    fake.info = {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "Dana",
        "picture": "https://lh3.googleusercontent.com/a",
    }
    c.get("/auth/google/login", params={"next": nxt}, follow_redirects=False)
    return c.get("/auth/google/callback", follow_redirects=False)


def _app(tmp_path: Path, **extra: Any) -> tuple[Any, Database]:
    settings = _settings(tmp_path, **extra)
    app = create_app(settings, start_scheduler=False)
    return app, Database(settings.database.url)


def test_sign_in_creates_the_user_once(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        first = sign_in(c, fake)
        assert first.status_code == 303 and first.headers["location"] == "/events?welcome=1"
        assert "spotx_session" in first.cookies
        me = c.get("/api/me")
        assert me.status_code == 200 and me.headers["cache-control"] == "private, no-store"
        assert (
            me.json()["email"] == "dana@example.com" and me.json()["role"] == "user" and not me.json()["digest_opt_in"]
        )
        again = sign_in(c, fake)
        assert again.headers["location"] == "/events"
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(UserRow)) == 1
        assert s.scalar(select(func.count()).select_from(SessionRow)) == 2
        assert all(len(t) == 64 for t in s.scalars(select(SessionRow.token_hash)))  # only hashes kept


def test_refused_sign_ins(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        fake.info = {"sub": "g-2", "email": "x@example.com", "email_verified": False}
        c.get("/auth/google/login", params={"next": "/"}, follow_redirects=False)
        assert c.get("/auth/google/callback", follow_redirects=False).headers["location"] == "/?signin=unverified"
        sign_in(c, fake, email="same@example.com", sub="g-3")
        c.cookies.clear()
        taken = sign_in(c, fake, email="same@example.com", sub="g-4", nxt="/")
        assert taken.headers["location"] == "/?signin=taken"
        with db.session() as s:
            s.scalar(select(UserRow).where(UserRow.google_sub == "g-3")).blocked_at = utcnow()
        assert sign_in(c, fake, email="same@example.com", sub="g-3", nxt="/").headers["location"] == "/?signin=blocked"


def test_safe_next() -> None:
    assert google.safe_next("//evil.com") == "/"
    assert google.safe_next("https://evil.com") == "/"
    assert google.safe_next("/\\evil.com") == "/"
    assert google.safe_next(None) == "/"
    assert google.safe_next("/events?a=1") == "/events?a=1"


def test_me_edit_sign_out_and_delete(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/me").status_code == 401
        sign_in(c, fake)
        assert c.patch("/api/me", json={"digest_opt_in": True}).status_code == 403  # not from our pages
        assert (
            c.patch("/api/me", json={"digest_opt_in": True}, headers=OURS | {"Origin": "https://evil.com"}).status_code
            == 403
        )
        edited = c.patch(
            "/api/me", json={"digest_opt_in": True, "locale": "en", "name": " Dana L "}, headers=OURS
        ).json()
        assert edited["digest_opt_in"] and edited["locale"] == "en" and edited["name"] == "Dana L"
        with db.session() as s:
            assert s.scalar(select(UserRow.digest_opt_in_at)) is not None  # proof of consent
        assert c.post("/api/auth/logout", headers=OURS).status_code == 204
        assert c.get("/api/me").status_code == 401
        sign_in(c, fake)
        assert c.delete("/api/me", headers=OURS).status_code == 204
        assert c.get("/api/me").status_code == 401
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(UserRow)) == 0
        assert s.scalar(select(func.count()).select_from(SessionRow)) == 0


def test_expired_and_blocked_sessions(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake)
        with db.session() as s:
            s.scalar(select(SessionRow)).expires_at = utcnow() - timedelta(seconds=1)
        assert c.get("/api/me").status_code == 401
        sign_in(c, fake)
        with db.session() as s:
            s.scalar(select(UserRow)).blocked_at = utcnow()
        assert c.get("/api/me").status_code == 401  # a block signs the user out at once


def test_admin_through_google(tmp_path: Path, fake: FakeGoogle) -> None:
    settings = _settings(tmp_path)
    settings.admin.token = SecretStr("tok")
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/admin/api/users").status_code == 401
        sign_in(c, fake)  # a plain user
        assert c.get("/admin/api/users").status_code == 401
        c.cookies.clear()
        sign_in(c, fake, email=ADMIN.upper(), sub="g-admin")
        assert c.get("/api/me").json()["role"] == "admin"
        users = c.get("/admin/api/users").json()
        assert users["total"] == 2 and {u["email"] for u in users["items"]} == {"dana@example.com", ADMIN}
        c.cookies.clear()
        assert c.get("/admin/api/users", headers={"Authorization": "Bearer tok"}).status_code == 200  # break-glass


def test_admin_users_actions(tmp_path: Path, fake: FakeGoogle) -> None:
    app, _db = _app(tmp_path)
    with TestClient(app) as user, TestClient(app) as admin:
        sign_in(user, fake)
        dana = user.get("/api/me").json()["id"]
        sign_in(admin, fake, email=ADMIN, sub="g-admin")
        assert admin.get("/admin/api/users", params={"q": "DANA@"}).json()["total"] == 1
        detail = admin.get(f"/admin/api/users/{dana}").json()
        assert detail["sessions"] == 1 and not detail["blocked"]
        stats = admin.get("/admin/api/users/stats").json()
        assert (
            stats["total"] == 2
            and stats["new_7"] == 2
            and stats["admins"] == 1
            and sum(d["n"] for d in stats["per_day"]) == 2
        )
        admin.post(f"/admin/api/users/{dana}/block")
        assert user.get("/api/me").status_code == 401
        assert sign_in(user, fake, nxt="/").headers["location"] == "/?signin=blocked"
        admin.post(f"/admin/api/users/{dana}/unblock")
        sign_in(user, fake)
        admin.post(f"/admin/api/users/{dana}/role", json={"role": "admin"})
        assert user.get("/api/me").json()["role"] == "admin"
        admin.post(f"/admin/api/users/{dana}/revoke")
        assert user.get("/api/me").status_code == 401
        assert admin.delete(f"/admin/api/users/{dana}").json() == {"ok": True}
        assert admin.get(f"/admin/api/users/{dana}").status_code == 404


def test_sign_in_is_rate_limited(tmp_path: Path, fake: FakeGoogle) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        codes = [c.get("/auth/google/login", follow_redirects=False).status_code for _ in range(11)]
        assert codes[:10] == [302] * 10 and codes[10] == 429


def test_accounts_off_without_secrets(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, google_client_secret=None)
    with TestClient(app) as c:
        assert c.get("/auth/google/login", follow_redirects=False).status_code != 302
        assert c.get("/api/me").status_code == 401


def test_logs_never_carry_emails_or_tokens() -> None:
    assert scrub("GET /auth/google/callback?state=abc&code=4/0Ab hi dana@example.com") == (
        "GET /auth/google/callback?state=*** hi ***@***".replace("state=***", "state=***&code=***")
    )
    record = logging.LogRecord(
        "x", logging.INFO, "", 0, "%s - %s", ("1.2.3.4", "/api/email/unsubscribe?t=SECRET"), None
    )
    Redact().filter(record)
    assert record.getMessage() == "1.2.3.4 - /api/email/unsubscribe?t=***"
