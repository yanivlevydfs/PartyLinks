"""Sign in with Google: our page -> Google's page -> /auth/google/callback. Authlib checks the state,
nonce, PKCE and the ID token; no Google script ever loads on our pages (no consent needed for it)."""

from __future__ import annotations

import logging
import warnings
from typing import Any

# Authlib's own import of its httpx helper warns on stderr (an "error" line in Railway) on every start; it is
# Authlib's internal deprecation, nothing we call. Silenced for that one message only.
warnings.filterwarnings("ignore", message="The httpx module is deprecated")
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from partylinks.accounts import sessions
from partylinks.accounts.guard import limited
from partylinks.accounts.service import Blocked, EmailTaken, sign_in

log = logging.getLogger(__name__)
router = APIRouter(tags=["accounts"], include_in_schema=False, dependencies=[limited("auth", 10)])
oauth = OAuth()


def client(request: Request) -> Any:
    """The Google client, registered on first use with the app's settings."""
    if (google := oauth.create_client("google")) is not None:
        return google
    cfg = request.app.state.settings.accounts
    oauth.register(
        "google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret.get_secret_value(),
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    return oauth.create_client("google")


def safe_next(value: str | None) -> str:
    """Only a path on this site: no //host, no scheme, no backslash (an open redirect otherwise)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _with(target: str, param: str) -> str:
    return f"{target}{'&' if '?' in target else '?'}{param}"


@router.get("/auth/google/login")
async def login(request: Request, next: str | None = None) -> Response:
    request.session["next"] = safe_next(next)
    base = request.app.state.settings.site.base_url.rstrip("/")
    return await client(request).authorize_redirect(request, f"{base}/auth/google/callback")


def _finish(request: Request, info: dict[str, Any], target: str) -> Response:
    cfg = request.app.state.settings
    with request.app.state.db.session() as session:
        try:
            user, is_new = sign_in(
                session, sub=info["sub"], email=info["email"], name=info.get("name") or "",
                picture=info.get("picture"), locale=(info.get("locale") or "he")[:2],
                admin_emails=cfg.accounts.admin_emails,
            )  # fmt: skip
        except Blocked:
            return RedirectResponse(_with(target, "signin=blocked"), 303)
        except EmailTaken:
            return RedirectResponse(_with(target, "signin=taken"), 303)
        response = RedirectResponse(_with(target, "welcome=1") if is_new else target, 303)
        sessions.start(session, response, user.id, cfg.accounts.session_days, cfg.site.base_url.startswith("https://"))
        log.info("sign-in: user %s%s", user.id, " (new)" if is_new else "")
    return response


@router.get("/auth/google/callback")
async def callback(request: Request) -> Response:
    target = safe_next(request.session.pop("next", None))
    try:
        token = await client(request).authorize_access_token(request)
    except OAuthError as exc:
        log.info("sign-in refused: %s", exc.error)
        return RedirectResponse(_with(target, "signin=failed"), 303)
    info = token.get("userinfo") or {}
    if not info.get("sub") or not info.get("email") or not info.get("email_verified"):
        return RedirectResponse(_with(target, "signin=unverified"), 303)
    return await run_in_threadpool(_finish, request, dict(info), target)
