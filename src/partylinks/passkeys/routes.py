"""Passkey routes. Each ceremony is two calls: ``.../options`` hands the browser a fresh challenge (kept in
the signed sign-in cookie, 10 minutes, used once), ``.../verify`` checks the device's answer against it.

The relying party is the site's own host (``site.base_url``): a passkey made on spotx.fun works only there.
User verification is required both ways: the device must check the fingerprint, face or screen lock, not
only that someone touched it."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from partylinks.accounts import sessions
from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.models import UserRow
from partylinks.accounts.sessions import UserDep
from partylinks.db import utcnow
from partylinks.passkeys.models import PasskeyRow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/passkeys", tags=["accounts"], include_in_schema=False)
NO_STORE = {"Cache-Control": "private, no-store"}
OWN = [SameOrigin, limited("passkey", 20)]  # the signed-in user's own passkeys
SIGN_IN = [SameOrigin, limited("auth", 10)]  # the same budget as the Google and Facebook sign-in
STATE = {"register": "passkey_register", "login": "passkey_login"}  # keys in the signed sign-in cookie


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


class Ceremony(BaseModel):
    credential: dict[str, Any]  # the browser's PublicKeyCredential, as JSON (@simplewebauthn/browser)


class Registration(Ceremony):
    name: str = Field("", max_length=60)


def _party(request: Request) -> tuple[str, str]:
    """(rp_id, origin): the site's host, and the address its pages are served from."""
    base = request.app.state.settings.site.base_url.rstrip("/")
    return urlsplit(base).hostname or "localhost", base


def _options(request: Request, kind: str, options: Any) -> Response:
    """The options for the browser; their challenge is kept for the matching ``verify``."""
    request.session[STATE[kind]] = bytes_to_base64url(options.challenge)
    return Response(options_to_json(options), media_type="application/json", headers=NO_STORE)


def _challenge(request: Request, kind: str) -> bytes:
    """The challenge ``options`` handed out, once: a second ``verify`` with it finds none."""
    value = request.session.pop(STATE[kind], None)
    if not value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no passkey request in progress, try again")
    return base64url_to_bytes(value)


@router.get("", response_model=list[PasskeyOut])
def mine(user: UserDep, request: Request, response: Response) -> list[PasskeyOut]:
    response.headers.update(NO_STORE)
    with request.app.state.db.session() as session:
        rows = session.scalars(select(PasskeyRow).where(PasskeyRow.user_id == user.id).order_by(PasskeyRow.id))
        return [PasskeyOut.model_validate(row) for row in rows]


@router.delete("/{passkey_id}", status_code=204, dependencies=OWN)
def remove(passkey_id: int, user: UserDep, request: Request) -> Response:
    with request.app.state.db.session() as session:
        session.execute(delete(PasskeyRow).where(PasskeyRow.id == passkey_id, PasskeyRow.user_id == user.id))
    return Response(status_code=204)


@router.post("/register/options", dependencies=OWN)
def register_options(user: UserDep, request: Request) -> Response:
    rp_id, _ = _party(request)
    with request.app.state.db.session() as session:
        have = session.scalars(select(PasskeyRow.credential_id).where(PasskeyRow.user_id == user.id)).all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=request.app.state.settings.site.name,
        user_id=user.id.encode(),  # our random id, never the email: the device stores this handle
        user_name=user.email,
        user_display_name=user.name,
        # A discoverable key (the device lists it, no email typed) that checks the fingerprint / face / lock.
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in have],  # one per device
    )
    return _options(request, "register", options)


@router.post("/register/verify", response_model=PasskeyOut, dependencies=OWN)
def register_verify(body: Registration, user: UserDep, request: Request, response: Response) -> PasskeyOut:
    rp_id, origin = _party(request)
    challenge = _challenge(request, "register")
    try:
        verified = verify_registration_response(
            credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, require_user_verification=True,
        )  # fmt: skip
    except WebAuthnException as exc:
        log.info("passkey not added for user %s: %s", user.id, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the passkey could not be verified") from exc
    transports = (body.credential.get("response") or {}).get("transports") or []
    known = {t.value for t in AuthenticatorTransport}
    with request.app.state.db.session() as session:
        credential_id = bytes_to_base64url(verified.credential_id)
        if session.scalar(select(PasskeyRow.id).where(PasskeyRow.credential_id == credential_id)) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "this passkey is already added")
        row = PasskeyRow(
            user_id=user.id, credential_id=credential_id, public_key=verified.credential_public_key,
            sign_count=verified.sign_count, transports=",".join(t for t in transports if t in known),
            name=body.name.strip()[:60] or "Passkey",
        )  # fmt: skip
        session.add(row)
        session.flush()
        out = PasskeyOut.model_validate(row)
    log.info("passkey added for user %s", user.id)
    response.headers.update(NO_STORE)
    return out


@router.post("/login/options", dependencies=SIGN_IN)
def login_options(request: Request) -> Response:
    rp_id, _ = _party(request)
    # No allow list: the device offers the passkeys it holds for this site, the visitor picks one.
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    return _options(request, "login", options)


@router.post("/login/verify", status_code=204, dependencies=SIGN_IN)
def login_verify(body: Ceremony, request: Request) -> Response:
    rp_id, origin = _party(request)
    challenge = _challenge(request, "login")
    cfg = request.app.state.settings
    try:
        credential = parse_authentication_credential_json(body.credential)
    except WebAuthnException as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed passkey answer") from exc
    with request.app.state.db.session() as session:
        found = session.execute(
            select(PasskeyRow, UserRow)
            .join(UserRow, UserRow.id == PasskeyRow.user_id)
            .where(PasskeyRow.credential_id == bytes_to_base64url(credential.raw_id))
        ).first()
        if found is None:  # removed from the account, or its account deleted: the device still offers it
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown passkey")
        passkey, user = found
        handle = credential.response.user_handle
        if handle is not None and handle != user.id.encode():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "the passkey could not be verified")
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
                credential_public_key=passkey.public_key, credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )  # fmt: skip
        except WebAuthnException as exc:
            log.info("passkey sign-in refused for user %s: %s", user.id, exc)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "the passkey could not be verified") from exc
        if user.blocked_at is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "blocked")
        now = utcnow()
        passkey.sign_count, passkey.last_used_at, user.last_login_at = verified.new_sign_count, now, now
        response = Response(status_code=204)
        sessions.start(session, response, user.id, cfg.accounts.session_days, cfg.site.base_url.startswith("https://"))
        log.info("sign-in (passkey): user %s", user.id)
    return response
