"""Passkeys (partylinks.passkeys): add one while signed in, sign in with it, the checks that refuse a bad answer.
A software authenticator stands in for the phone: a P-256 key, "none" attestation, the bytes a browser sends."""

from __future__ import annotations

import hashlib
import json
import secrets
import struct
from pathlib import Path
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from webauthn.helpers import bytes_to_base64url

from partylinks.accounts.models import UserRow
from partylinks.db import utcnow
from partylinks.passkeys.models import PasskeyRow
from tests.test_accounts import OURS, FakeGoogle, _app, sign_in

RP_ID, ORIGIN = "testserver", "http://testserver"
UP, UV, AT = 0x01, 0x04, 0x40


class Device:
    """One phone's passkey for this site."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(16)
        self.count = 0
        self.handle: str | None = None  # the user handle, set on create

    def _client_data(self, kind: str, options: dict[str, Any]) -> bytes:
        return json.dumps({"type": kind, "challenge": options["challenge"], "origin": ORIGIN}).encode()

    def create(self, options: dict[str, Any], flags: int = UP | UV | AT) -> dict[str, Any]:
        self.handle = options["user"]["id"]
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth = hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        auth += bytes(16) + struct.pack(">H", len(self.id)) + self.id + cose
        return {
            "id": bytes_to_base64url(self.id), "rawId": bytes_to_base64url(self.id), "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options)),
                "attestationObject": bytes_to_base64url(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})),
                "transports": ["internal", "hybrid", "bogus"],
            },
        }  # fmt: skip

    def get(self, options: dict[str, Any], flags: int = UP | UV, bump: bool = True) -> dict[str, Any]:
        self.count += bump
        client = self._client_data("webauthn.get", options)
        auth = hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": bytes_to_base64url(self.id), "rawId": bytes_to_base64url(self.id), "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client), "authenticatorData": bytes_to_base64url(auth),
                "signature": bytes_to_base64url(signature), "userHandle": self.handle,
            },
        }  # fmt: skip


def _add(c: TestClient, device: Device, name: str = "iPhone") -> Any:
    options = c.post("/api/passkeys/register/options", headers=OURS).json()
    return c.post(
        "/api/passkeys/register/verify", json={"credential": device.create(options), "name": name}, headers=OURS
    )


def _login(c: TestClient, device: Device, **kw: Any) -> Any:
    options = c.post("/api/passkeys/login/options", headers=OURS).json()
    assert (
        options["rpId"] == RP_ID and options["userVerification"] == "required" and not options.get("allowCredentials")
    )
    return c.post("/api/passkeys/login/verify", json={"credential": device.get(options, **kw)}, headers=OURS)


def test_add_a_passkey_then_sign_in_with_it(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    phone = Device()
    with TestClient(app) as c:
        assert c.post("/api/passkeys/register/options", headers=OURS).status_code == 401  # signed out
        sign_in(c, fake)
        options = c.post("/api/passkeys/register/options", headers=OURS).json()
        assert options["authenticatorSelection"]["residentKey"] == "required"
        assert options["authenticatorSelection"]["userVerification"] == "required"
        added = c.post(
            "/api/passkeys/register/verify", json={"credential": phone.create(options), "name": "iPhone"}, headers=OURS
        )
        assert added.status_code == 200 and added.json()["name"] == "iPhone"
        assert [p["name"] for p in c.get("/api/passkeys").json()] == ["iPhone"]
        again = c.post("/api/passkeys/register/options", headers=OURS).json()
        assert [e["id"] for e in again["excludeCredentials"]] == [bytes_to_base64url(phone.id)]  # one per device

        assert c.post("/api/auth/logout", headers=OURS).status_code == 204
        assert c.get("/api/me").status_code == 401
        done = _login(c, phone)
        assert done.status_code == 204 and "spotx_session" in done.cookies
        assert c.get("/api/me").json()["email"] == "dana@example.com"
    with db.session() as s:
        row = s.scalar(select(PasskeyRow))
        assert row is not None
        assert row.sign_count == 1 and row.last_used_at is not None and row.transports == "internal,hybrid"


def test_refused_passkey_answers(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    phone, stranger = Device(), Device()
    with TestClient(app) as c:
        sign_in(c, fake)
        app.state.guard.over_limit = lambda *a, **k: False  # the answers, not the sign-in rate limit
        no_uv = c.post("/api/passkeys/register/options", headers=OURS).json()
        refused = c.post(
            "/api/passkeys/register/verify", json={"credential": phone.create(no_uv, flags=UP | AT)}, headers=OURS
        )
        assert refused.status_code == 400  # touched, but no fingerprint / face / lock checked
        assert _add(c, phone).status_code == 200
        c.post("/api/auth/logout", headers=OURS)

        assert c.post("/api/passkeys/login/options").status_code == 403  # not from our pages
        assert _login(c, phone, flags=UP).status_code == 400  # no user verification
        assert _login(c, phone).status_code == 204
        c.post("/api/auth/logout", headers=OURS)
        assert _login(c, phone, bump=False).status_code == 400  # the counter didn't move: a cloned key
        assert _login(c, stranger).status_code == 401  # a passkey we never saw

        options = c.post("/api/passkeys/login/options", headers=OURS).json()
        answer = {"credential": phone.get(options)}
        assert c.post("/api/passkeys/login/verify", json=answer, headers=OURS).status_code == 204
        c.post("/api/auth/logout", headers=OURS)
        assert c.post("/api/passkeys/login/verify", json=answer, headers=OURS).status_code == 400  # challenge used once

        with db.session() as s:
            user = s.scalar(select(UserRow))
            assert user is not None
            user.blocked_at = utcnow()
        assert _login(c, phone).status_code == 403


def test_remove_and_delete_account(tmp_path: Path, fake: FakeGoogle) -> None:
    app, db = _app(tmp_path)
    phone, laptop = Device(), Device()
    with TestClient(app) as c:
        sign_in(c, fake, email="other@example.com", sub="g-2")
        theirs = _add(c, laptop, "Windows").json()["id"]
        c.post("/api/auth/logout", headers=OURS)
        sign_in(c, fake)
        mine = _add(c, phone).json()["id"]
        assert c.delete(f"/api/passkeys/{theirs}", headers=OURS).status_code == 204  # not theirs to remove: no-op
        assert c.delete(f"/api/passkeys/{mine}", headers=OURS).status_code == 204
        assert c.get("/api/passkeys").json() == []
        c.post("/api/auth/logout", headers=OURS)
        assert _login(c, phone).status_code == 401  # removed: the phone still offers it, it signs in to nothing
        assert _login(c, laptop).status_code == 204
        assert c.delete("/api/me", headers=OURS).status_code == 204
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(PasskeyRow)) == 0  # gone with the account
