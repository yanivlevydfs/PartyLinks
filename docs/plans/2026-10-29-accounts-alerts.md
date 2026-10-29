# Accounts, Saved Events, Alerts and Email: Implementation Plan

**Status:** executed 2026-10-29 (phases 0–5, commits `65f8f66` to `10c4f6c`). Kept as the record of the
plan; the code differs in the points under "Deviations" at the end. How it works now:
[`docs/accounts.md`](../accounts.md).

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** optional Google accounts on SPOTX, with saved events (a heart), event alerts ("save search"), a notification bell, a "For you" row and email (welcome, instant, a weekly digest on Thursday at 10:00), plus admin pages for users and email, and updated legal pages.

**Design:** [`docs/accounts-alerts-design.md`](../accounts-alerts-design.md) holds every decision (D1–D13). Read it first.

**Architecture:** the CheapFlights concept, ported to the PartyLinks stack (SQLAlchemy on the shared `Base`, FastAPI routers in their own packages, Vite + React). Each feature is a new package: `accounts/`, `saved/`, `alerts/` and `mail/` on the backend, and the matching folders in `web/src/`. Existing files only get hook lines. Alerts match through the **same filter builder `/events` uses**. The worker runs the matcher and email jobs.

**Tech stack:** FastAPI, SQLAlchemy 2, PostgreSQL (pgserver in tests), APScheduler (worker), Authlib (Google OAuth redirect), itsdangerous, resend, Jinja2, React + react-query + zustand.

---

## Ground rules (read once)

- **Login is never required.** No existing feature moves behind sign-in (memory rule `login-optional`).
- **New features in their own files.** Existing files only get the hook lines named in each task.
- **Concurrent sessions commit to master.** Before every commit, run `git pull --rebase` and check `git status`. Stage **named files only**, never `git add -A`.
- **Tests:** `python -m pytest tests/<file> -v`. Every test runs on an empty PostgreSQL (`tests/conftest.py`). App tests use `settings_for`, `seed` and `event` from `tests/test_storage_api.py`, as `tests/test_plans.py` does.
- **Web:** `cd web && npm run build`. Then **restart the local server** (a build breaks a running localhost).
- **DB docs:** every new table or column also goes into `docs/postgres-migration.md` and its updates log.
- **Never log** an email address, Google `sub`, token or cookie. Log `user.id` only.
- **Commit messages:** plain sentences like the existing log (no `feat:` prefix), ending with the `Co-Authored-By` line.
- **CheapFlights source** is at `H:\GitHub\cheapflights`. Each task names the file to read before writing.

---

# Phase 0: Shared filter builder

### Task 0.1: Move the `/events` filter builder to module level

`event_filters` is a closure inside `create_app` (`src/partylinks/api.py`, around line 907) that needs a `Request`. The worker has no request. Move its body into a plain function and keep a thin dependency wrapper, so `/events` and `/events/map` behave exactly as before.

**Files:**
- Modify: `src/partylinks/api.py` (the `event_filters` closure, around lines 907–1039)
- Test: `tests/test_event_filters.py` (new)

**Step 1: Write the failing test**

```python
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
        n = session.scalar(select(func.count()).select_from(EventRow).where(*build_event_filters(settings, free_entry=True)))
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/events", params={"free_entry": "true"}).json()["total"] == n == 3
```

**Step 2: Run it.** `python -m pytest tests/test_event_filters.py -v`. Expected: FAIL with `ImportError: cannot import name 'build_event_filters'`.

**Step 3: Implement**
- Add a module-level function `build_event_filters(cfg: Settings, *, sounds_like: Sequence[str] = (), source=None, genre=None, …, q=None) -> list[ColumnElement[bool]]`, above `create_app`. It takes **the same keyword parameters with the same defaults** as the closure, but plain types (no `Annotated[..., Query]`). The body is the closure's body with `request.app.state.settings` replaced by `cfg` and `_sounds_like(request, q)` replaced by `sounds_like`.
- Reduce the closure to a wrapper. Keep its full `Annotated[..., Query(...)]` signature, so the OpenAPI docs do not change:

```python
    def event_filters(request: Request, source: ... , ..., q: ... = None) -> list[ColumnElement[bool]]:
        """WHERE clauses of the event filters, shared by the list and the map."""
        return build_event_filters(
            request.app.state.settings, sounds_like=_sounds_like(request, q) if q else (),
            source=source, genre=genre, artist=artist, venue=venue, venue_slug=venue_slug, city=city,
            in_target_city=in_target_city, abroad=abroad, country=country, anywhere=anywhere,
            entry_type=entry_type, free_entry=free_entry, sold_out=sold_out, discount=discount,
            day_of_week=day_of_week, time_slot=time_slot, area=area, region=region, weekend=weekend,
            on_date=on_date, upcoming=upcoming, include_duplicates=include_duplicates,
            date_from=date_from, date_to=date_to, min_price=min_price, max_price=max_price, q=q,
        )
```

**Step 4: Run** `python -m pytest tests/test_event_filters.py tests/test_storage_api.py -v`, then the full `python -m pytest`. Expected: all PASS, with the same count as before plus 1.

**Step 5: Commit**

```bash
git pull --rebase
git add src/partylinks/api.py tests/test_event_filters.py
git commit -m "The /events filter builder is a plain function (build_event_filters), so the alert matcher can reuse it"
```

---

# Phase 1: Accounts (and the legal pages, which ship in the same phase)

### Task 1.1: Dependencies

**Files:** `pyproject.toml`, `requirements.txt`, `requirements.lock`

**Step 1:** Add the following to `pyproject.toml` `dependencies` and to `requirements.txt`:
```
    "authlib>=1.3",
    "itsdangerous>=2.2",
    "resend>=2.0",
    "jinja2>=3.1",
```

**Step 2:** Regenerate the lock with the command in its header:
`uv pip compile pyproject.toml --python-version 3.12 --python-platform linux -o requirements.lock`

**Step 3:** `pip install -e .`, then `python -c "import authlib, itsdangerous, resend, jinja2"`. Expected: no output.

**Step 4: Commit** the three files: "Accounts dependencies: authlib, itsdangerous, resend, jinja2 (lock regenerated)".

### Task 1.2: Settings

**Files:**
- Create: `src/partylinks/accounts/__init__.py` (docstring only: "Optional Google accounts (see docs/accounts-alerts-design.md).")
- Create: `src/partylinks/accounts/config.py`
- Create: `src/partylinks/mail/__init__.py`, `src/partylinks/mail/config.py`
- Modify: `src/partylinks/config.py`, adding **two hook lines** in `Settings` and two imports
- Modify: `config/settings.yaml`, adding the `accounts:` and `mail:` blocks with comments, as in design §13

**Code:** `accounts/config.py`:

```python
"""accounts: in settings.yaml; secrets from the environment only (PARTYLINKS_ACCOUNTS__...)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class AccountsSettings(BaseModel):
    enabled: bool = True  # false: no account UI, /auth answers 404, the account jobs idle
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = None  # signs the sign-in state cookie and unsubscribe links
    session_days: int = Field(30, ge=1, le=365)
    max_alerts: int = Field(20, ge=1)
    max_saved: int = Field(500, ge=1)
    admin_emails: list[str] = Field(default_factory=list)  # promoted to admin on sign-in

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.google_client_id and self.google_client_secret and self.session_secret)
```

`mail/config.py`:

```python
"""mail: in settings.yaml; the Resend key from the environment only (PARTYLINKS_MAIL__RESEND_API_KEY)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class MailSettings(BaseModel):
    resend_api_key: SecretStr | None = None  # missing: every email is off and the admin warns
    from_address: str = "SPOTX <events@spotx.fun>"
    digest_cron: str = "0 10 * * thu"  # in settings.timezone
    digest_max_events: int = Field(12, ge=1, le=30)
    instant_min_interval_minutes: int = Field(60, ge=5)
    matcher_interval_minutes: int = Field(15, ge=1)
```

Hooks in `config.py` `Settings`:

```python
    accounts: AccountsSettings = Field(default_factory=AccountsSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
```

Also extend `_mask` in `admin.py`, so the config view never shows `accounts.google_client_secret`, `accounts.session_secret` or `mail.resend_api_key`. Read `_mask` first (around line 320) and follow how it hides `admin.token`.

**Test:** add to `tests/test_accounts.py` (new):

```python
from partylinks.config import Settings


def test_accounts_not_ready_without_secrets() -> None:
    assert not Settings().accounts.ready
    s = Settings(accounts={"google_client_id": "x", "google_client_secret": "y", "session_secret": "z"})
    assert s.accounts.ready
```

Run, then commit: "Settings for accounts and mail (secrets from env only, masked in the admin)".

### Task 1.3: Account tables

Read first: CheapFlights `backend/users_db.py:54-275` (DDL) and `delete_user` (line 531).

**Files:**
- Create: `src/partylinks/accounts/models.py`
- Modify: `src/partylinks/db.py`, adding the hook `import partylinks.accounts.models` next to `import partylinks.plans.models` (around line 886)

```python
"""The account tables. On the app's one Base, so Database.create_schema creates them (db.py imports this)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class UserRow(Base):
    """A Google account. ``id`` is ours (random); ``google_sub`` and ``email`` are never logged."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # secrets.token_hex(16)
    google_sub: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text, unique=True)  # lowercased
    name: Mapped[str] = mapped_column(Text)
    picture: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(2), default="he")
    role: Mapped[str] = mapped_column(String(10), default="user")  # user | admin
    blocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    digest_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)  # Communications Law §30A: explicit
    digest_opt_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_digest_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_instant_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    welcome_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    last_login_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SessionRow(Base):
    """A signed-in browser. Only the SHA-256 of the cookie is kept; deleting the row signs it out."""

    __tablename__ = "user_sessions"  # "sessions" reads like the SQLAlchemy kind

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
```

**Test** (`tests/test_accounts.py`): create the schema, insert a user with a session, delete the user, and check the session is gone (the cascade works on PostgreSQL). Run, then commit: "Account tables: users and user_sessions (cascade on delete)".

### Task 1.4: Sessions and the current-user dependency

Read first: CheapFlights `backend/dependencies.py` (cookie, token hash) and `users_db.create_session` / `is_session_valid`.

**Files:**
- Create: `src/partylinks/accounts/sessions.py`
- Test: `tests/test_accounts.py`

```python
"""Session cookies: a random token in the browser, its SHA-256 in user_sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import SessionRow, UserRow
from partylinks.db import utcnow

COOKIE = "spotx_session"
ROLL_AFTER = timedelta(days=1)  # move the expiry forward at most once a day


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start(session: Session, response: Response, user: UserRow, days: int, secure: bool) -> None:
    token = secrets.token_urlsafe(32)
    session.add(SessionRow(user_id=user.id, token_hash=_hash(token), expires_at=utcnow() + timedelta(days=days)))
    response.set_cookie(COOKIE, token, max_age=days * 86400, httponly=True, secure=secure, samesite="lax", path="/")


def end(session: Session, request: Request, response: Response) -> None:
    if token := request.cookies.get(COOKIE):
        session.execute(delete(SessionRow).where(SessionRow.token_hash == _hash(token)))
    response.delete_cookie(COOKIE, path="/")


def user_for(session: Session, token: str | None, days: int) -> UserRow | None:
    """The signed-in user, or None: no cookie, unknown or expired token, or a blocked user."""
    if not token:
        return None
    row = session.execute(
        select(SessionRow, UserRow).join(UserRow).where(SessionRow.token_hash == _hash(token))
    ).first()
    if row is None:
        return None
    s, user = row
    now = utcnow()
    if s.expires_at <= now or user.blocked_at is not None:
        return None
    if now - s.last_seen_at > ROLL_AFTER:
        s.last_seen_at, s.expires_at = now, now + timedelta(days=days)
    return user


def _db(request: Request) -> Any:
    with request.app.state.db.session() as session:
        yield session


DbDep = Annotated[Session, Depends(_db)]


def optional_user(request: Request, session: DbDep) -> UserRow | None:
    return user_for(session, request.cookies.get(COOKIE), request.app.state.settings.accounts.session_days)


def current_user(user: Annotated[UserRow | None, Depends(optional_user)]) -> UserRow:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in")
    return user


UserDep = Annotated[UserRow, Depends(current_user)]
```

**Tests:** a `start` then `user_for` round trip; an expired session returns None; a blocked user returns None; a wrong token returns None. Run, then commit: "Account sessions: hashed tokens, rolling 30 days, blocked users signed out".

### Task 1.5: Google sign-in (Authlib redirect)

Read first: CheapFlights `backend/routers/auth.py:28-96` (what happens on sign-in: upsert, welcome scheduling, admin by email).

**Files:**
- Create: `src/partylinks/accounts/google.py` (OAuth client and the two routes)
- Create: `src/partylinks/accounts/service.py` (`sign_in`, `delete_user`)
- Modify: `src/partylinks/api.py`, two hooks next to the plans router (around line 1165):

```python
    from partylinks.accounts import install as install_accounts
    install_accounts(app, settings)  # SessionMiddleware for the sign-in state + the account routers
```

`accounts/__init__.py`, `install`:

```python
def install(app: FastAPI, settings: Settings) -> None:
    from starlette.middleware.sessions import SessionMiddleware

    from partylinks.accounts.google import router as google_router
    from partylinks.accounts.routes import router as me_router

    if settings.accounts.ready:
        secure = settings.site.base_url.startswith("https://")
        # Only Authlib's state, nonce and PKCE verifier, for the 10 minutes of a sign-in.
        app.add_middleware(SessionMiddleware, secret_key=settings.accounts.session_secret.get_secret_value(),
                           session_cookie="spotx_signin", max_age=600, same_site="lax", https_only=secure)
        app.include_router(google_router)
    app.include_router(me_router)  # /api/me answers 401 when accounts are off
```

> `add_middleware` must run before the app starts. `create_app` already adds middleware before it includes routers, so put the hook **before** `app.add_middleware(CachedPages)` if Starlette complains. Check that the middleware order still has `SecurityMiddleware` outermost.

`accounts/service.py`:

```python
"""Signing in and deleting an account."""

from __future__ import annotations

import secrets

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.db import utcnow


class Blocked(Exception):
    pass


class EmailTaken(Exception):
    """Same email, a different Google account: never merged silently."""


def sign_in(session: Session, *, sub: str, email: str, name: str, picture: str | None,
            locale: str, admin_emails: list[str]) -> tuple[UserRow, bool]:
    """The user for this Google account, created on the first sign-in. Returns (user, is_new)."""
    email = email.strip().lower()
    user = session.scalar(select(UserRow).where(UserRow.google_sub == sub))
    is_new = user is None
    if user is None:
        if session.scalar(select(UserRow.id).where(UserRow.email == email)):
            raise EmailTaken
        user = UserRow(id=secrets.token_hex(16), google_sub=sub, email=email, name=name[:100],
                       picture=picture, locale=locale if locale in ("he", "en") else "he")
        session.add(user)
    if user.blocked_at is not None:
        raise Blocked
    user.email, user.picture, user.last_login_at = email, picture, utcnow()
    if email in {e.lower() for e in admin_emails}:
        user.role = "admin"
    session.flush()
    return user, is_new


def delete_user(session: Session, user_id: str) -> None:
    """Sessions, saved events, alerts and their hits go by ON DELETE CASCADE."""
    session.execute(delete(UserRow).where(UserRow.id == user_id))
```

`accounts/google.py`:

```python
"""Sign in with Google: our page -> Google -> /auth/google/callback. No Google script on our pages."""

from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from partylinks.accounts import sessions
from partylinks.accounts.service import Blocked, EmailTaken, sign_in

log = logging.getLogger(__name__)
router = APIRouter(tags=["accounts"], include_in_schema=False)
oauth = OAuth()
_registered = False


def _client(request: Request):
    global _registered
    cfg = request.app.state.settings.accounts
    if not _registered:
        oauth.register(
            "google",
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret.get_secret_value(),
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )
        _registered = True
    return oauth.google


def safe_next(value: str | None) -> str:
    """Only a path on this site: no //host, no scheme (an open redirect otherwise)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


@router.get("/auth/google/login")
async def login(request: Request, next: str | None = None):
    request.session["next"] = safe_next(next)
    base = request.app.state.settings.site.base_url.rstrip("/")
    return await _client(request).authorize_redirect(request, f"{base}/auth/google/callback")


@router.get("/auth/google/callback")
async def callback(request: Request):
    target = safe_next(request.session.pop("next", None))
    try:
        token = await _client(request).authorize_access_token(request)
    except OAuthError as exc:
        log.info("sign-in refused by Google: %s", exc.error)
        return RedirectResponse(f"{target}?signin=failed", 303)
    info = token.get("userinfo") or {}
    if not info.get("email_verified"):
        return RedirectResponse(f"{target}?signin=unverified", 303)
    cfg = request.app.state.settings
    response = RedirectResponse(target, 303)
    with request.app.state.db.session() as session:
        try:
            user, is_new = sign_in(session, sub=info["sub"], email=info["email"], name=info.get("name") or "",
                                   picture=info.get("picture"), locale=(info.get("locale") or "he")[:2],
                                   admin_emails=cfg.accounts.admin_emails)
        except Blocked:
            return RedirectResponse(f"{target}?signin=blocked", 303)
        except EmailTaken:
            return RedirectResponse(f"{target}?signin=taken", 303)
        sessions.start(session, response, user, cfg.accounts.session_days, cfg.site.base_url.startswith("https://"))
        if is_new:
            response.headers["location"] = f"{target}{'&' if '?' in target else '?'}welcome=1"
        log.info("sign-in: user %s%s", user.id, " (new)" if is_new else "")
    return response
```

**Tests** (`tests/test_accounts.py`). Monkeypatch `oauth.google.authorize_access_token` (call `_client` once first, or patch `OAuth.create_client`) to an `async def` that returns `{"userinfo": {"sub": "g1", "email": "A@x.com", "email_verified": True, "name": "Dana"}}`. Then check:
- `GET /auth/google/callback` sets `spotx_session`, redirects to `/?welcome=1`, and creates a user with email `a@x.com`;
- a second call returns no `welcome=1` and creates no second user;
- `email_verified: False` redirects with `signin=unverified` and creates no user;
- a blocked user redirects with `signin=blocked`;
- an email in `admin_emails` gets `role == "admin"`;
- `safe_next("//evil.com") == "/"`, `safe_next("https://x") == "/"`, `safe_next("/events?a=1") == "/events?a=1"`.

Use `Settings(accounts={"google_client_id": "x", "google_client_secret": "y", "session_secret": "z" * 32}, site={"base_url": "http://testserver"})` on top of `settings_for`.

Commit: "Sign in with Google through a server redirect (Authlib, PKCE, verified email only); no Google script on our pages".

### Task 1.6: `/api/me` (profile, sign out, delete)

Read first: CheapFlights `routers/auth.py:97-155`.

**Files:**
- Create: `src/partylinks/accounts/routes.py`
- Test: `tests/test_accounts.py`

```python
"""The signed-in user's own account: read, edit, sign out, delete."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from partylinks.accounts import sessions
from partylinks.accounts.service import delete_user
from partylinks.accounts.sessions import DbDep, UserDep
from partylinks.db import utcnow


def same_origin(request: Request) -> None:
    """Writes come from our own pages only: our header, and our Origin when the browser sends one."""
    base = request.app.state.settings.site.base_url.rstrip("/")
    origin = request.headers.get("origin")
    if request.headers.get("x-requested-with") != "spotx" or (origin and origin.rstrip("/") != base):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")


router = APIRouter(prefix="/api", tags=["accounts"], include_in_schema=False)
NO_STORE = {"Cache-Control": "private, no-store"}


class MeOut(BaseModel):
    id: str
    name: str
    email: str
    picture: str | None
    locale: str
    role: str
    digest_opt_in: bool


class MePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    locale: Literal["he", "en"] | None = None
    digest_opt_in: bool | None = None


@router.get("/me", response_model=MeOut)
def me(user: UserDep, response: Response) -> MeOut:
    response.headers.update(NO_STORE)
    return MeOut.model_validate(user, from_attributes=True)


@router.patch("/me", response_model=MeOut, dependencies=[Depends(same_origin)])
def edit_me(body: MePatch, user: UserDep, session: DbDep, response: Response) -> MeOut:
    if body.name is not None:
        user.name = body.name.strip()
    if body.locale is not None:
        user.locale = body.locale
    if body.digest_opt_in is not None and body.digest_opt_in != user.digest_opt_in:
        user.digest_opt_in = body.digest_opt_in
        user.digest_opt_in_at = utcnow() if body.digest_opt_in else None  # proof of consent (§30A)
    session.merge(user)
    response.headers.update(NO_STORE)
    return MeOut.model_validate(user, from_attributes=True)


@router.post("/auth/logout", status_code=204, dependencies=[Depends(same_origin)])
def logout(request: Request, session: DbDep) -> Response:
    response = Response(status_code=204)
    sessions.end(session, request, response)
    return response


@router.delete("/me", status_code=204, dependencies=[Depends(same_origin)])
def delete_me(request: Request, user: UserDep, session: DbDep) -> Response:
    response = Response(status_code=204)
    sessions.end(session, request, response)
    delete_user(session, user.id)
    return response
```

> `UserDep` and `DbDep` must share **one** DB session per request. FastAPI caches a dependency within a request, so `current_user -> optional_user -> _db` and `DbDep` resolve to the same session. Assert this in a test (edit, then re-read).

**Tests:**
- signed out, `/api/me` returns 401;
- signed in, it returns the profile with `Cache-Control: private, no-store`;
- `PATCH` without `X-Requested-With` returns 403;
- `PATCH digest_opt_in=true` sets `digest_opt_in_at`;
- logout, then `/api/me` returns 401;
- delete, then the user and sessions rows are gone.

A helper `signed_in(client)` runs the mocked callback from Task 1.5.

Commit: "/api/me: profile, digest consent with its time, sign out, delete account (cross-site writes refused)".

### Task 1.7: Rate limits on `/auth` and `/api/me`

**Files:**
- Modify: `src/partylinks/accounts/routes.py` and `google.py` (a dependency)
- Read: `src/partylinks/security.py:201` (`over_limit(ip, bucket, limit, now, seconds=60)`, clock `time.monotonic()`). The guard is reachable as `request.state.guard` (set at line 221). Use the client IP the middleware already resolved (`request.client.host`, after `_forwarded_client`).

```python
def limited(bucket: str, per_minute: int):
    def check(request: Request) -> None:
        guard = getattr(request.state, "guard", None)
        if guard and guard.over_limit(request.client.host, bucket, per_minute, time.monotonic()):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "slow down")
    return Depends(check)
```

Apply `limited("auth", 10)` to both `/auth/google/*` routes, and `limited("me", 30)` to every write in the `/api/me*` routes, including saved events and alerts later.

**Test:** the 11th login in a minute returns 429. Commit: "Rate limits on sign-in and account writes".

### Task 1.8: Admins through Google

**Files:**
- Modify: `src/partylinks/admin.py`, `require_admin` (line 45). Add the hook at the top of the function:

```python
    from partylinks.accounts.sessions import COOKIE, user_for
    if (cookie := request.cookies.get(COOKIE)) and request.app.state.settings.accounts.enabled:
        with request.app.state.db.session() as s:
            user = user_for(s, cookie, request.app.state.settings.accounts.session_days)
            if user is not None and user.role == "admin":
                return
```

- Modify: `web/src/pages/Admin.tsx`. When `/api/me` says `role === "admin"`, skip the token prompt and send requests without a bearer token (the cookie goes along on its own).

**Tests:** admin cookie gets `/admin/api/overview` 200; user cookie gets 401 when a token is set; token alone still 200 (break-glass).

Commit: "Admins can sign in with Google (role admin); the token stays as a fallback".

### Task 1.9: Admin users API

Read first: CheapFlights `backend/routers/admin.py` (the users section) and `users_db.list_users` / `active_session_counts`.

**Files:**
- Create: `src/partylinks/accounts/admin_routes.py`, an `APIRouter(prefix="/admin/api", dependencies=[AdminDep])`, included from `accounts.install`
- Test: `tests/test_accounts_admin.py`

Endpoints (design §8): `GET /users?q=&page=` (25 a page; `ILIKE` on email or name; columns: id, name, email, picture, role, blocked, digest_opt_in, created_at, last_login_at, sessions, alerts, saved). The `alerts` and `saved` counts are 0 until Phases 2–3 add them; use `select(func.count())` subqueries added in those phases. Also:
- `GET /users/{id}`;
- `POST /users/{id}/revoke`;
- `POST /users/{id}/block` (sets `blocked_at` and deletes sessions);
- `POST /users/{id}/unblock`;
- `POST /users/{id}/role` with body `{"role": "user"|"admin"}`;
- `DELETE /users/{id}` (calls `service.delete_user`);
- `GET /users/stats?days=30` (total, new in 7 and 30 days, opted-in, signups per day as `[{day, n}]` grouped by `created_at` in Asia/Jerusalem).

**Tests:** search finds by part of the email; block signs the user out (their `/api/me` returns 401); role change; delete; stats counts.

Commit: "Admin users API: search, detail, revoke, block, role, delete, stats".

### Task 1.10: Web account UI

Read first: CheapFlights `frontend/src/context/AuthContext.tsx`, `components/UserProfileMenu.tsx`, `components/ProfileClient.tsx`.

**Files (new):** `web/src/accounts/useMe.ts`, `SignInButton.tsx`, `UserMenu.tsx`, `AccountPage.tsx`, `WelcomeSheet.tsx`, `accounts.css`, `i18n.ts`
**Hooks:**
- `web/src/ui.tsx`: in `<header className="top">` (line 112), add `<AccountSlot/>`. It renders `UserMenu` when signed in, else `SignInButton`, and nothing when `/api/config` says accounts are off.
- `web/src/main.tsx`: add the route `/account`.

`useMe.ts`:

```ts
import { useQuery, useQueryClient } from "@tanstack/react-query";

export interface Me { id: string; name: string; email: string; picture: string | null; locale: "he" | "en"; role: string; digest_opt_in: boolean }

/** Our own writes: the header the server checks for (cross-site requests can't set it). */
export async function send(url: string, method: string, body?: unknown) {
  const res = await fetch(url, {
    method, credentials: "same-origin",
    headers: { "X-Requested-With": "spotx", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok && res.status !== 204) throw new Error(String(res.status));
  return res.status === 204 ? null : res.json();
}

export function useMe() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => { const r = await fetch("/api/me"); return r.ok ? r.json() : null; },
    staleTime: 5 * 60_000,
  });
}

export const signInUrl = () => `/auth/google/login?next=${encodeURIComponent(location.pathname + location.search)}`;

export function useSignOut() {
  const qc = useQueryClient();
  return async () => { await send("/api/auth/logout", "POST"); qc.setQueryData(["me"], null); qc.invalidateQueries(); };
}
```

- **`SignInButton`** is a plain `<a href={signInUrl()}>` styled as a button, with the Google "G" as inline SVG and the text "המשך עם Google" / "Continue with Google". Google's branding guidelines allow this text. It loads no Google script.
- **`UserMenu`**: avatar (`picture`, else initials), with a menu of: my alerts, saved, account, admin (if role admin), sign out. Esc closes it, focus is trapped, and it uses `aria-haspopup`/`aria-expanded`.
- **`AccountPage`**: name, email (read-only), email language, the digest checkbox (wired in Phase 4; until then, hidden when `mail` is off), and "Delete account", which needs the word "delete" or "מחק" typed. It shows `?signin=failed|blocked|taken|unverified` as a message.
- **`WelcomeSheet`** opens on `?welcome=1`. For now it only says hello. The digest opt-in and first alert are added in Phases 3–4. Closing it removes the parameter.

**Check:** `cd web && npm run build`, restart local, then:
- sign in with a real Google test client on localhost (redirect URI `http://localhost:<port>/auth/google/callback`);
- the avatar appears;
- `/account` works;
- sign out works;
- delete works;
- the site works the same signed out, and a 360 px width shows no overflow.

Commit: "Account UI: Continue with Google, user menu, account page (edit, sign out, delete)".

### Task 1.11: Admin users tab

Read first: CheapFlights `frontend/src/components/AdminDashboardClient.tsx` (users section).

**Files:** create `web/src/accounts/AdminUsers.tsx`. Hook `web/src/pages/Admin.tsx`: add `"users"` to `TABS` (line 59) and `{tab === "users" && <AdminUsers />}`.

The tab has stats cards, signups per day (reuse the clicks tab's chart component), a search box, and a paged table. A row opens a drawer with revoke / block / role / delete; delete needs the email typed.

Build, check, then commit: "Admin users tab".

### Task 1.12: Legal pages, consent list, robots (ship with Phase 1)

Read first: CheapFlights `docs/privacy-policy.md` and `docs/terms-and-conditions.md` (§2, §13 and the deletion and rights sections), and the design doc §14.

**Files:**
- `web/src/pages/Privacy.tsx`, in HE and EN: the sections from design §14. Saved events and alerts are listed now, so the text is not edited again in each phase.
- `web/src/pages/Terms.tsx`: accounts optional, age 16+, one account per person, blocking, alerts best effort.
- `web/src/consent.tsx`: storage list rows `spotx_session` (necessary, 30 days), `spotx_signin` (necessary, 10 minutes, sign-in only) and `spotx-saved` (necessary, until cleared; Phase 2).
- `robots.txt` (find where it is served: `grep -rn "robots" src/partylinks/seo.py`): `Disallow: /account`, `/alerts`, `/saved`, `/auth/`.
- Mark `noindex` on `/account`, `/alerts` and `/saved`, the same way `Admin.tsx` does.
- `docs/legal.md`: update the table, "server-side records" and the open items (privacy email, 2017 security regulations, lawyer review).
- `src/partylinks/admin.py` overview warnings (hook): "Google sign-in not configured" when `accounts.enabled and not accounts.ready`.

Build, then read both pages in HE and EN. Commit: "Privacy, terms and the storage list cover accounts, saved events, alerts and email".

### Task 1.13: Docs and deploy

- `docs/postgres-migration.md` and its updates log: `users`, `user_sessions`.
- `docs/configuration.md`: the `accounts` and `mail` blocks and the env vars.
- `docs/admin.md`: the users tab and Google admin sign-in.
- New `docs/accounts.md`: a short how-it-works with a link to the design.
- **Before the push:** Google Cloud console OAuth client (Web), with redirect URIs `https://spotx.fun/auth/google/callback` and localhost. In Railway (service web), set `PARTYLINKS_ACCOUNTS__GOOGLE_CLIENT_ID`, `__GOOGLE_CLIENT_SECRET`, `__SESSION_SECRET` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) and `__ADMIN_EMAILS`.
- Commit the docs, then `git pull --rebase && git push`. Check the Railway deploy logs, then sign in on spotx.fun.

---

# Phase 2: Saved events (the heart)

### Task 2.1: `saved_events` table and API

Read first: CheapFlights `backend/routers/saved_items.py`.

**Files:**
- Create: `src/partylinks/saved/__init__.py`, `saved/models.py`, `saved/routes.py`
- Modify: `src/partylinks/db.py`, two hooks: `import partylinks.saved.models`, and inside `delete_events` (line 868) `delete_saved(session, chunk)`
- Modify: `src/partylinks/accounts/__init__.py` `install`, which includes `saved.routes.router`
- Test: `tests/test_saved.py`

Model:

```python
class SavedEventRow(Base):
    """A heart. No foreign key to events (like plans): retention deletes the row with its event."""

    __tablename__ = "saved_events"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
```

Routes:
- `GET /api/me/saved` returns event cards, upcoming only, soonest first. Build the cards the way `/events` does: `select(EventRow).options(selectinload(EventRow.images), defer(...))`, then `_summary_extras` and `EventSummaryOut`, imported late from `partylinks.api` as `plans/routes.py` does. Resolve each saved id to its **group primary** through `group_id`.
- `PUT /api/me/saved/{event_id}`: `insert ... on conflict do nothing`; 404 for an unknown event; 409 over `max_saved`.
- `DELETE /api/me/saved/{event_id}`.
- `POST /api/me/saved/merge` with `{ids: [...]}` (at most 500): keeps only known, upcoming ids, inserts them, and returns the list.
- `GET /api/events/cards?ids=a&ids=b` (public, at most 100, through `_cached`): signed-out hearts. Put it in `saved/routes.py`, with the router included from `api.py` as plans is.
- `delete_saved(session, event_ids)` for retention.

**Tests:**
- save, list, unsave;
- unknown event returns 404;
- merge drops past and unknown ids;
- a regrouped event lists its primary;
- `delete_events` removes the hearts;
- deleting the user removes the hearts;
- `/api/events/cards` caps at 100.

Commit: "Saved events: heart API, merge on sign-in, public cards for signed-out hearts, gone with their event".

### Task 2.2: Heart UI

Read first: CheapFlights `frontend/src/context/SavedItemsContext.tsx` and `components/SavedFlightsClient.tsx`.

**Files (new):** `web/src/saved/useSaved.ts`, `SaveHeart.tsx`, `SavedPage.tsx`, `saved.css`, `i18n.ts`
**Hooks:**
- the event card component in `web/src/ui.tsx`;
- `pages/EventPage.tsx`;
- the map popup in `pages/MapPage.tsx`;
- `main.tsx`: route `/saved`, and a header link "Saved (n)" in `ui.tsx`.

`useSaved.ts` (core):

```ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
// Signed out: the ids live on this device. Signed in: the server, toggled optimistically.
// On sign-in, the device's list is merged into the account once, then cleared.
export const useLocalSaved = create<{ ids: string[]; toggle: (id: string) => void; clear: () => void }>()(
  persist((set) => ({
    ids: [],
    toggle: (id) => set((s) => ({ ids: s.ids.includes(id) ? s.ids.filter((x) => x !== id) : [id, ...s.ids].slice(0, 500) })),
    clear: () => set({ ids: [] }),
  }), { name: "spotx-saved" }),
);
```

`useSaved()` returns `{ isSaved(id), toggle(id), count }`:
- signed out, it uses `useLocalSaved`;
- signed in, it uses react-query `["saved"]` with `PUT`/`DELETE` through `send()` and an optimistic update.

A `useEffect` on sign-in, when the local ids are not empty, POSTs `merge` and then calls `clear()`.

`SaveHeart` is a `<button aria-pressed>` with the label "שמור אירוע" / "Save event". Clicks stop propagation so the card link doesn't open.

`SavedPage` uses `/api/me/saved` signed in, or `/api/events/cards?ids=` signed out. Signed out, it shows a soft note: "Sign in to keep them on every device". It is `noindex`.

Build, then check:
- signed out: heart, reload, still saved;
- sign in: the heart is merged;
- a second browser shows the same list;
- 360 px;
- keyboard only.

Commit: "Heart on every event (works signed out, merged on sign-in) and /saved".

### Task 2.3: Admin and docs

- Add a `saved` count to `/admin/api/users` and to the detail (the last 20 hearts).
- `docs/postgres-migration.md`: `saved_events`.

Commit, then push and check the deploy.

---

# Phase 3: Alerts ("save search"), bell, For you

### Task 3.1: Criteria

Read first: CheapFlights `backend/alerts/types.py` (`CRITERIA_FIELDS`, `validate_criteria`).

**Files:** create `src/partylinks/alerts/__init__.py` and `alerts/criteria.py`. Test: `tests/test_alerts.py`.

```python
"""What an alert looks for: a subset of the site's filters (web/src/store.ts Filters), relative dates kept
relative ("weekend" is always the coming weekend), turned into build_event_filters arguments when matched."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

WHEN = ("all", "weekend", "week", "month")


class Criteria(BaseModel):
    q: str = Field("", max_length=100)  # artist, venue or a word
    genres: list[str] = Field(default_factory=list, max_length=20)
    regions: list[str] = Field(default_factory=list, max_length=20)
    areas: list[str] = Field(default_factory=list, max_length=20)
    city: str | None = Field(None, max_length=60)
    slots: list[str] = Field(default_factory=list, max_length=4)
    freeOnly: bool = False
    discountOnly: bool = False
    when: str = "all"  # a preset, or YYYY-MM-DD~YYYY-MM-DD

    @field_validator("when")
    @classmethod
    def _when(cls, v: str) -> str:
        if v in WHEN:
            return v
        start, _, end = v.partition("~")
        date.fromisoformat(start), date.fromisoformat(end)  # ValueError -> 422
        return v

    def is_empty(self) -> bool:
        return self == Criteria()

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()

    def params(self, today: date) -> dict[str, Any]:
        """build_event_filters keyword arguments; the same mapping as filterParams in web/src/Filters.tsx."""
        p: dict[str, Any] = {
            "q": self.q if len(self.q) >= 2 else None, "genre": self.genres or None, "region": self.regions or None,
            "area": self.areas or None, "city": self.city, "time_slot": self.slots or None,
            "free_entry": True if self.freeOnly else None, "discount": True if self.discountOnly else None,
        }
        end = lambda days: datetime.combine(today + timedelta(days=days), time(23, 59, 59))  # noqa: E731
        if self.when == "weekend":
            p |= {"weekend": True, "date_to": end(7)}
        elif self.when == "week":
            p["date_to"] = end(6)
        elif self.when == "month":
            p["date_to"] = end(29)
        elif "~" in self.when:
            a, b = self.when.split("~")
            p |= {"date_from": datetime.combine(date.fromisoformat(a), time.min),
                  "date_to": datetime.combine(date.fromisoformat(b), time(23, 59, 59))}
        return p
```

"Tonight" and a single day are left out of `WHEN` on purpose: an alert is ongoing.

Validate against the facets in the route (Task 3.3). Unknown genres and regions return 422, using the same lists `/categories` serves.

**Tests:**
- `Criteria(when="weekend").params(date(2026,10,29))` gives `{"weekend": True, "date_to": datetime(2026,11,5,23,59,59)}`;
- a bad `when` raises;
- `digest()` is stable and ignores key order;
- `is_empty()`.

Commit: "Alert criteria: the site's filters, kept relative, turned into /events filter arguments".

### Task 3.2: Alert tables

Read first: CheapFlights `backend/alerts/schema.py`.

**Files:** create `src/partylinks/alerts/models.py`. Hooks in `db.py`: `import partylinks.alerts.models`, and `delete_hits(session, chunk)` in `delete_events`.

```python
class AlertRow(Base):
    __tablename__ = "event_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    criteria: Mapped[dict[str, Any]] = mapped_column(NullableJSON)
    criteria_hash: Mapped[str] = mapped_column(String(64), index=True)
    instant: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AlertHitRow(Base):
    """This event matched this alert. The key is what stops an event being sent twice; it is also the bell."""

    __tablename__ = "alert_hits"

    alert_id: Mapped[int] = mapped_column(ForeignKey("event_alerts.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)  # no FK: retention deletes it
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    matched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    instant_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    digest_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AlertStateRow(Base):
    """One row: where the matcher stopped (events first seen after this are new)."""

    __tablename__ = "alert_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    cursor: Mapped[datetime] = mapped_column(UTCDateTime)
```

`AlertHitRow.user_id` has no FK, but the alert FK cascades, so deleting a user removes the alerts and their hits.

**Test:** deleting a user removes alerts and hits; `delete_events` removes hits. Commit.

### Task 3.3: Alerts API, bell, For you

Read first: CheapFlights `backend/routers/alerts.py`, `backend/alerts/repo.py`, `backend/routers/notifications.py`.

**Files:** create `src/partylinks/alerts/routes.py`, included from `accounts.install`. Test: `tests/test_alerts.py`.

All endpoints use `UserDep`. Writes use `same_origin` and `limited("me", 30)`, and responses send `NO_STORE`.
- `GET /api/me/alerts`.
- `POST /api/me/alerts` takes `{criteria, name?, instant?}`. It refuses empty criteria and unknown genres or regions, and returns 409 over `max_alerts`. The name is auto-built from the criteria labels when missing. It stores `criteria_hash`.
- `PATCH /api/me/alerts/{id}`: name, criteria (rehash), instant, active.
- `DELETE /api/me/alerts/{id}`.
- `GET /api/me/alerts/{id}/preview`: `{total, items[:6]}` using `build_event_filters(cfg, **Criteria(...).params(today))`, upcoming, primary only.
- `GET /api/me/bell`: `{unseen, items}`, the last 30 hits joined to event cards, newest first.
- `POST /api/me/bell/seen`: sets `seen_at = now` on all unseen hits.
- `GET /api/me/for-you`: up to 12 upcoming events that match **any** active alert, built as `or_(*[and_(*build_event_filters(cfg, **c.params(today))) for c in criteria])`, ordered by `start_at`. Cache it per user for 10 minutes with `cachetools.TTLCache(maxsize=10_000, ttl=600)` keyed by `user_id`, and clear the entry on any alert write.

**Tests:**
- CRUD;
- limit;
- unknown genre returns 422;
- another user's alert returns 404;
- preview total equals `/events` with the same params;
- bell unseen goes to 0 after `seen`;
- for-you returns events from both alerts' criteria.

Commit: "Alerts API: save a search, preview, bell, For you".

### Task 3.4: The matcher (worker)

Read first: CheapFlights `backend/alerts/matcher.py` (`run_matcher`, `_fire`).

**Files:**
- Create: `src/partylinks/alerts/matcher.py`
- Create: `src/partylinks/alerts/jobs.py` (`schedule(scheduler, settings, service)`)
- Modify: `src/partylinks/scheduler.py` `build_scheduler`, one hook before `return scheduler`:
  `from partylinks.alerts.jobs import schedule as schedule_alerts; schedule_alerts(scheduler, settings, service)`
- Create: `scripts/time_matcher.py` (production-size timing)
- Test: `tests/test_alerts.py`

```python
"""New events x active alerts -> alert_hits. Matching is build_event_filters itself, so an alert finds
exactly what the same filters show on the site."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from partylinks.accounts.models import UserRow
from partylinks.alerts.criteria import Criteria
from partylinks.alerts.models import AlertHitRow, AlertRow, AlertStateRow
from partylinks.api import build_event_filters
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upcoming_filter, utcnow

log = logging.getLogger(__name__)


def run(settings: Settings, db: Database) -> int:
    """One pass. Returns the number of new hits."""
    now = utcnow()
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    with db.session() as session:
        state = session.get(AlertStateRow, 1) or AlertStateRow(id=1, cursor=now)
        session.add(state)
        new_ids = list(session.scalars(select(EventRow.id).where(
            EventRow.first_seen_at > state.cursor, EventRow.is_primary.is_(True), upcoming_filter())))
        state.cursor = now
        if not new_ids:
            return 0
        alerts = session.execute(
            select(AlertRow.id, AlertRow.user_id, AlertRow.criteria, AlertRow.criteria_hash)
            .join(UserRow, UserRow.id == AlertRow.user_id)
            .where(AlertRow.active.is_(True), UserRow.blocked_at.is_(None))
        ).all()
        by_hash: dict[str, list[tuple[int, str]]] = defaultdict(list)
        criteria: dict[str, Criteria] = {}
        for alert_id, user_id, raw, digest in alerts:
            by_hash[digest].append((alert_id, user_id))
            criteria.setdefault(digest, Criteria.model_validate(raw))
        # ponytail: one query per distinct criteria over this pass's new ids (usually < 500).
        # Ceiling ~50k distinct criteria per pass; batch by genre/region with VALUES joins if timing says so.
        rows = []
        for digest, owners in by_hash.items():
            where = build_event_filters(settings, **criteria[digest].params(today))
            for event_id in session.scalars(select(EventRow.id).where(EventRow.id.in_(new_ids), *where)):
                rows += [{"alert_id": a, "event_id": event_id, "user_id": u, "matched_at": now} for a, u in owners]
        for start in range(0, len(rows), 5000):
            session.execute(insert(AlertHitRow).values(rows[start:start + 5000]).on_conflict_do_nothing())
        log.info("alerts: %d new events, %d alerts, %d hits", len(new_ids), len(alerts), len(rows))
        return len(rows)
```

The first pass after deploy starts from `now`, so it never floods users with events that already existed.

`jobs.schedule` adds `IntervalTrigger(minutes=settings.mail.matcher_interval_minutes)`, `max_instances=1`, `coalesce=True`, id `alerts:match`, but only when `settings.accounts.enabled`. That job then runs `mail.instant` (Phase 4; until then, nothing). Running the matcher once after each source run is a later optimisation; skip it in v1, since the 15-minute interval covers it.

**Tests:**
- **parity:** seed 5 events, create alerts with 3 different criteria, set the cursor before the seed, run, and each alert's hits equal `/events` with `Criteria.params()` (ids);
- a second run adds no hits;
- two users with the same criteria get one query (assert with a counter monkeypatched on `build_event_filters`) and both get hits;
- a blocked user gets no hits;
- a paused alert gets no hits.

**Production-size timing (required before the push):** `scripts/time_matcher.py` seeds about 28k events and 50k random alerts (genres and regions drawn from the config), sets the cursor so 500 events are "new", and prints the pass time. Run it locally on pgserver, and note the number in the commit message. If a pass takes more than 60 s, do the VALUES batching before shipping.

Commit: "Alert matcher in the worker every 15 min: new events x alerts, through the /events filters; measured N s at 28k events / 50k alerts".

### Task 3.5: Alerts UI, save search, bell, For you

Read first: CheapFlights `frontend/src/components/AlertsClient.tsx`, `AlertForm.tsx`, `AlertCard.tsx`, `hooks/useAlerts.ts`, `NotificationBell.tsx`, `NotificationsClient.tsx`, `deals/TierAlertCta.tsx`.

**Files (new):** `web/src/alerts/{types.ts, useAlerts.ts, AlertsPage.tsx, AlertForm.tsx, AlertCard.tsx, SaveAlertButton.tsx, Bell.tsx, ForYouRow.tsx, alerts.css, i18n.ts}`

**Hooks:**

| File | Hook |
| --- | --- |
| `main.tsx` | Route `/alerts` |
| `ui.tsx` header | `<Bell/>` when signed in |
| `Filters.tsx` | `<SaveAlertButton filters={f}/>` next to the result count |
| `pages/Quiz.tsx` | Last step: "Save these answers as an alert" |
| `pages/MapPage.tsx` | `<SaveAlertButton/>` with the map's filters |
| `pages/EventPage.tsx` | "Alert me about {artist}" / "{venue}" (criteria `{q}`) |
| `pages/Home.tsx` | `<ForYouRow/>` when signed in and at least one alert exists |
| `accounts/WelcomeSheet.tsx` | "Create your first alert from these filters" |

- **`SaveAlertButton`** turns `Filters` into `Criteria`: it keeps `q, genres, regions, areas, city, slots, freeOnly, discountOnly` and maps `when` to `weekend`, `week`, `month`, a range, or `all`; `tonight` and single days become `all`. Signed out, it opens a small sheet: "Sign in to get these events by email", with a `SignInButton` whose `next` is the current URL plus `&save_alert=1`. After sign-in, that parameter creates the alert automatically.
- **`AlertForm`** reuses `GenrePicker`, `Calendar` and the region pickers from `Filters.tsx` (import them; never copy). It has a live "N events match now" (the preview endpoint, debounced 400 ms) and an "Email me right away" toggle (instant).
- **`Bell`** polls `/api/me/bell` with `refetchInterval: 60_000` and `refetchIntervalInBackground: false`. It shows a badge and a dropdown of event cards with hearts. Opening it POSTs `seen`. The count is in an `aria-live="polite"` region.
- **`ForYouRow`** uses the same row component as the home rows. Respect the image-request budget from memory `rate-limit-page-weight`: lazy-load images and limit the row to 12.

**Landing pages (SEO):** the React landing pages get a `SaveAlertButton` preset with that page's filter. The server-rendered HTML gets a plain link `/auth/google/login?next=<page>?save_alert=1` labelled "Get these events every Thursday". Find the renderer with `grep -rn "def render" src/partylinks/seo.py`.

Build, then check every surface in HE and EN at 360 px and by keyboard. Commit: "Save search as an alert from every surface, /alerts, the bell and the For you row".

### Task 3.6: Admin and docs

- Admin users list and detail: the alert count, the alerts with their criteria, and the last 50 hits.
- Stats: top alert genres, regions and `q` words (counted from `event_alerts.criteria`).
- Docs: `postgres-migration.md` (`event_alerts`, `alert_hits`, `alert_state`) and `docs/accounts.md`.

Commit, then push. Check the Railway logs for the `alerts:` line every 15 minutes.

---

# Phase 4: Email (waits for the Resend account)

**Preconditions:** the Resend account exists and spotx.fun is verified (SPF, DKIM and DMARC records at the DNS host). The key is set in Railway as `PARTYLINKS_MAIL__RESEND_API_KEY`. Until then, every task here still builds and tests with Resend mocked, and production stays silent.

### Task 4.1: Sender, runs table, switches, unsubscribe tokens

Read first: CheapFlights `backend/email_service.py:1-120` (idempotency, `is_account_error`, `_log_send_failure`) and `dependencies.make_unsubscribe_token`.

**Files:**
- Create: `src/partylinks/mail/models.py`: `EmailRunRow` (design §6), and `MailSwitchesRow` (one row: `digest`, `instant` and `welcome` booleans, default true). The design said `worker_state`, but that row is rewritten every heartbeat, so the switches get their own row.
- Create: `src/partylinks/mail/send.py`
- Create: `src/partylinks/mail/tokens.py`
- Hook: `db.py`, `import partylinks.mail.models`
- Test: `tests/test_mail.py`

`tokens.py`:

```python
"""Unsubscribe links: {user id, kind} signed with accounts.session_secret; no expiry (law: must keep working)."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from partylinks.config import Settings


def _s(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.accounts.session_secret.get_secret_value(), salt="unsubscribe")


def make(settings: Settings, user_id: str, kind: str) -> str:
    return _s(settings).dumps({"u": user_id, "k": kind})


def read(settings: Settings, token: str) -> tuple[str, str] | None:
    try:
        data = _s(settings).loads(token)
    except BadSignature:
        return None
    return data["u"], data["k"]
```

`send.py`: `send_batch(settings, messages: list[dict]) -> tuple[int, int]`. It splits the messages into chunks of 100, calls `resend.Batch.send(chunk, options={"idempotency_key": ...})` (check the exact batch signature in the installed `resend` package: `python -c "import resend, inspect; print(inspect.signature(resend.Batch.send))"`), and returns `(sent, failed)`. An account or key error raises `MailAccountError`, which stops the run; per-message errors are counted. Every message gets the headers `List-Unsubscribe: <{base}/api/email/unsubscribe?t={token}>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. Logs use user ids only.

**Tests:** token round trip, and a tampered token returns None; `send_batch` with `resend.Batch.send` monkeypatched splits 250 messages into 3 calls and sets the headers; no `@` appears in captured logs (`caplog`).

Commit: "Mail: Resend batch sender, send runs, switches, signed unsubscribe links".

### Task 4.2: Templates

Read first: CheapFlights `backend/email_templates/cheap_report_he.html`, `welcome_he.html`, `email_service._build_deal_cards_html`.

**Files:**
- Create: `src/partylinks/mail/templates/{base.html, welcome.html, instant.html, digest.html}`. One template per kind, with the language strings from `mail/i18n.py`. Set `dir="rtl"` for HE.
- Create: `src/partylinks/mail/render.py`: a Jinja2 `Environment(autoescape=True)` with a `render(kind, user, events, saved)` function that returns `(subject, html, text)`.

Each event card has:
- the image `{base}/images/{image_id}/640.webp` (WebP rule);
- date and time, using `time_known` (date only when false);
- the venue;
- **weather**, read from the `weather.py` cache (`grep -n "def " src/partylinks/weather.py` and reuse its lookup; never fetch during a send);
- the price or "free";
- a button to the event page with `utm_source=email&utm_medium=email&utm_campaign={kind}`, built through `utm.py`.

The footer has the sender name and contact address, why you got this email, and unsubscribe (§30A).

**Tests:**
- `render("digest", …)` contains every event title, `dir="rtl"` for `he`, the unsubscribe URL, and a weather line;
- HTML in a title is escaped.

Commit: "Email templates (HE/EN): event cards with image, time, venue, weather and price; §30A footer".

### Task 4.3: Welcome, instant and digest jobs

Read first: CheapFlights `backend/scheduler.py:293-340` (`_send_due_cheap_report_emails`) and `docs/superpowers/specs/2026-07-25-welcome-email-design.md`.

**Files:**
- Create: `src/partylinks/mail/jobs.py` with `welcome(settings, db)`, `instant(settings, db)` and `digest(settings, db)`, each recording an `email_runs` row.
- Modify: `src/partylinks/alerts/jobs.py`, which schedules them:
  - welcome on an interval of 5 minutes;
  - instant right after each matcher pass, in the same job;
  - digest on `CronTrigger.from_crontab(settings.mail.digest_cron, timezone=settings.timezone)`.
  
  All of them do nothing when `mail.resend_api_key` is missing or their switch is off.

How each job picks its users:
- **welcome:** users with `welcome_sent_at IS NULL` and `created_at < now - 2 min`, not blocked. Stamp `welcome_sent_at` after each sent chunk.
- **instant:** users with hits where `instant_sent_at IS NULL` on an alert with `instant = true`, and `last_instant_at` NULL or older than `instant_min_interval_minutes`. One email per user, with all their pending instant hits (at most 10 cards). Stamp `instant_sent_at` and `last_instant_at`. Idempotency key `instant:{user}:{YYYYMMDDHH}`.
- **digest:** users with `digest_opt_in`, not blocked, and `last_digest_at` NULL or before this week's run. The content, in order:
  - saved events in the next 7 days;
  - then hits with `digest_sent_at IS NULL` on upcoming events in the next 10 days, deduplicated by `group_id`, up to `digest_max_events`;
  - then, if fewer than 4, "This weekend's free events" (`build_event_filters(cfg, free_entry=True, weekend=True, date_to=...)`).
  
  Stamp `digest_sent_at` on the hits and `last_digest_at` on the user after a successful send. Idempotency key `digest:{user}:{ISO year-week}`.

**Tests** (Resend mocked):
- welcome goes once;
- instant respects the 1-per-hour cap;
- digest skips users who did not opt in;
- digest puts saved events first;
- a rerun in the same week sends nothing;
- the empty filler appears;
- a missing key sends nothing and records no run.

Commit: "Welcome, instant and weekly digest emails (Thu 10:00), once each, capped, behind admin switches".

### Task 4.4: Unsubscribe endpoints

Read first: CheapFlights `routers/auth.py:156-228` and `frontend/scripts/check-unsubscribe-route.mjs`.

**Files:** create `src/partylinks/mail/routes.py`, included from `accounts.install`. The endpoints are exempt from `same_origin`, since they come from mail clients.
- `GET /api/email/unsubscribe?t=` returns a small HE/EN HTML page with a confirm button that POSTs back. It does **not** unsubscribe on GET, because link scanners prefetch links.
- `POST /api/email/unsubscribe?t=` (RFC 8058 one-click): kind `digest` sets `digest_opt_in = false` and `digest_opt_in_at = None`; kind `instant` sets `instant = false` on all of that user's alerts. Returns 200.

**Tests:** GET changes nothing; POST works; a bad token returns 400. Commit.

### Task 4.5: Admin email tab and preview

**Files:** create `src/partylinks/mail/admin_routes.py` and `web/src/accounts/AdminEmail.tsx`. Hook: `Admin.tsx` `TABS` gains `"email"`.

The endpoints are from design §8: `GET /email`, `POST /email/switches`, `POST /email/test?kind=` (sends to the signed-in admin; with a token-only admin, it needs `?to=<user id>`), and `GET /email/preview?user_id=&kind=` (returns the rendered HTML, shown in an iframe with `srcdoc` and `sandbox`). The admin overview warns "Resend key missing" when `accounts.ready` is true but there is no key.

**Tests:** preview returns HTML with the user's events; the switches persist; a test send calls Resend once. Commit.

### Task 4.6: Opt-in UI

- `AccountPage`: the digest checkbox ("Send me the weekly digest every Thursday", unticked by default) and a note on instant emails per alert.
- `WelcomeSheet`: the same checkbox and "create your first alert".
- `/account`: shows `?unsubscribed=digest` as a message.

Build, then check. Commit, then push, once the Resend key is in Railway. Use **Send test to me** from the admin, and check Gmail in HE and EN, RTL, images, weather, and the unsubscribe header ("Unsubscribe" shows next to the sender).

---

# Phase 5: Launch

### Task 5.1: Every-surface check (design §11 checklist)

Tick every line: filters, quiz, map, event page, landing pages (React and server-rendered), home For you, the heart everywhere, HE and EN strings, robots and noindex, accessibility (keyboard, `aria`), and 360 px.

### Task 5.2: Blog post

Write `content/blog/<slug>.html` (HE) **and** `content/blog/en/<slug>.html` (EN), "Get the parties you love every Thursday", following `docs/seo-and-blog.md`.

### Task 5.3: Docs

Final pass over `docs/accounts.md`, `docs/admin.md`, `docs/legal.md`, `docs/configuration.md` and `docs/postgres-migration.md` (with its updates log), and set the design doc's status to "Implemented".

### Task 5.4: Deploy and verify

Push, then check the Railway deploy logs, then go through these on spotx.fun:
- sign in, heart, alert, bell after the next matcher pass;
- the Thursday digest;
- admin users and email tabs;
- `/health` still `ok`.

Recheck the 0-scan index list on 2026-11-04 (memory `unused-indexes-check`) together with the new tables.

---

## Done when

- Every phase is shipped and deployed, and `python -m pytest` is green.
- The production-size matcher timing is recorded.
- The legal pages are live with Phase 1.
- The open items in design §20 are tracked (lawyer review, the 2017 security regulations, privacy email).

## Deviations (what was built differently)

- **Saved events:** a 🔔 save bell (`web/src/saved/SaveButton.tsx`), not a heart (`SaveHeart.tsx` was not
  kept). Saving needs an account: signed out, the sign-in popup (`web/src/accounts/SignInSheet.tsx`), and the
  event is saved back from Google through `?save_event=<id>` (`SavedLink.tsx`). localStorage `spotx-saved`
  is used only where Google sign-in is not configured. The header has a labelled "Saved" button with the
  count, the alert-news bell and the account menu (`AccountSlot.tsx`, instead of `SignInButton.tsx` and
  `UserMenu.tsx`).
- **Save as alert, signed out:** the criteria travel in `?save_alert=<json>` and the header bell saves them.
- **Matcher (Task 3.4):** one query per distinct criteria set measured 330 s at production size; built as
  one query per single filter value, combined in Python, with a `COPY` insert (27.3 s for 500 new events ×
  50k alerts at 28k events, `scripts/time_matcher.py`). It waits 30 minutes after an event is first seen
  and counts only new duplicate groups. No pass after each source run.
- **Criteria:** a one-letter word is no filter; an alert with nothing else set is refused.
- **Email:** from `SPOTX <events@spotx.fun>` (sender only), reply-to `contact@spotx.fun`; idempotency keys per
  run and chunk; the admin's test send goes only to the signed-in admin's own address.
- **Admin:** changes with an admin's Google session need `X-Requested-With: spotx`; a `401` is a bad-token
  strike only when an `Authorization` header was sent; tracebacks in logs are scrubbed too.
- **Not built:** purging expired sessions; the server-rendered landing-page "every Thursday" call to action.

The design record lists the same changes: [accounts-alerts-design.md, section 21](../accounts-alerts-design.md#21-changes-after-implementation).
