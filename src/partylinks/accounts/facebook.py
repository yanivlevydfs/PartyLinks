"""Sign in with Facebook: our page -> Facebook's page -> /auth/facebook/callback, then the Graph API's
/me for the name, email and picture. Like Google's, no Facebook script ever loads on our pages.

Also Meta's data deletion callback: a user who removes the app on Facebook gets their account deleted."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from httpx2 import HTTPError  # what Authlib talks through (tests/test_accounts.py test_authlib_has_httpx2)
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from partylinks.accounts.google import _with, finish, safe_next
from partylinks.accounts.guard import limited
from partylinks.accounts.models import UserRow
from partylinks.accounts.service import delete_user

log = logging.getLogger(__name__)
router = APIRouter(tags=["accounts"], include_in_schema=False)
SIGN_IN = [limited("auth", 10)]  # the bucket Google's sign-in counts on too
oauth = OAuth()
FIELDS = "id,name,email,picture.type(large)"


def client(request: Request) -> Any:
    """The Facebook client, registered on first use. Unversioned URLs: the app's own Graph API version."""
    if (facebook := oauth.create_client("facebook")) is not None:
        return facebook
    cfg = request.app.state.settings.accounts
    oauth.register(
        "facebook",
        client_id=cfg.facebook_app_id,
        client_secret=cfg.facebook_app_secret.get_secret_value(),
        authorize_url="https://www.facebook.com/dialog/oauth",
        access_token_url="https://graph.facebook.com/oauth/access_token",
        api_base_url="https://graph.facebook.com/",
        client_kwargs={"scope": "email public_profile", "token_endpoint_auth_method": "client_secret_post"},
    )
    return oauth.create_client("facebook")


@router.get("/auth/facebook/login", dependencies=SIGN_IN)
async def login(request: Request, next: str | None = None) -> Response:
    request.session["next"] = safe_next(next)
    base = request.app.state.settings.site.base_url.rstrip("/")
    return await client(request).authorize_redirect(request, f"{base}/auth/facebook/callback")


@router.get("/auth/facebook/callback", dependencies=SIGN_IN)
async def callback(request: Request) -> Response:
    target = safe_next(request.session.pop("next", None))
    try:
        facebook = client(request)
        token = await facebook.authorize_access_token(request)
        me = (await facebook.get("me", params={"fields": FIELDS}, token=token)).json()
    except (OAuthError, HTTPError, ValueError) as exc:
        log.info("facebook sign-in refused: %s", getattr(exc, "error", type(exc).__name__))
        return RedirectResponse(_with(target, "signin=failed"), 303)
    if not me.get("id"):
        return RedirectResponse(_with(target, "signin=failed"), 303)
    if not me.get("email"):  # an account opened with a phone number, or the email permission declined
        return RedirectResponse(_with(target, "signin=noemail"), 303)
    picture = ((me.get("picture") or {}).get("data") or {})
    info = {
        "sub": str(me["id"]), "email": me["email"], "name": me.get("name") or "",
        "picture": None if picture.get("is_silhouette") else picture.get("url"),
    }  # fmt: skip
    return await run_in_threadpool(finish, request, "facebook", info, target)


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def parse_signed_request(signed: str, secret: str) -> dict[str, Any] | None:
    """Meta's ``signed_request``: base64url(HMAC-SHA256 of the payload).base64url(JSON). None if forged."""
    try:
        sig, payload = signed.split(".", 1)
        data = json.loads(_b64(payload))
        good = hmac.compare_digest(_b64(sig), hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    except ValueError:  # no dot, bad base64, bad JSON
        return None
    if not good or not isinstance(data, dict) or str(data.get("algorithm", "")).upper() != "HMAC-SHA256":
        return None
    return data


def _delete(request: Request, facebook_id: str) -> None:
    with request.app.state.db.session() as session:
        user = session.scalar(select(UserRow).where(UserRow.facebook_id == facebook_id))
        if user is None:
            return
        if user.google_sub:  # also signs in with Google: only the Facebook link goes
            user.facebook_id = None
            log.info("facebook data deletion: user %s unlinked from Facebook", user.id)
        else:
            delete_user(session, user.id)
            log.info("facebook data deletion: user %s deleted", user.id)


@router.post("/auth/facebook/deletion", dependencies=[limited("fb-deletion", 30)])  # Meta's servers
async def deletion(request: Request) -> Response:
    """Meta calls this when a user removes the app or asks for their data: the account is deleted at once."""
    cfg = request.app.state.settings
    form = await request.form()
    data = parse_signed_request(str(form.get("signed_request") or ""), cfg.accounts.facebook_app_secret.get_secret_value())
    if data is None or not data.get("user_id"):
        return JSONResponse({"error": "bad signed_request"}, 400)
    await run_in_threadpool(_delete, request, str(data["user_id"]))
    code = secrets.token_hex(8)
    return JSONResponse({"url": f"{cfg.site.base_url.rstrip('/')}/privacy?deletion={code}", "confirmation_code": code})
