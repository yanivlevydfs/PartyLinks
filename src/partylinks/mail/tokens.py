"""Unsubscribe links: {user id, kind} signed with ``accounts.session_secret``. They never expire: the law
wants a refusal to keep working from any email ever sent."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from partylinks.config import Settings


def _serializer(settings: Settings) -> URLSafeSerializer:
    secret = settings.accounts.session_secret
    if secret is None:
        raise RuntimeError("accounts.session_secret is not set")
    return URLSafeSerializer(secret.get_secret_value(), salt="unsubscribe")


def make(settings: Settings, user_id: str, kind: str) -> str:
    return _serializer(settings).dumps({"u": user_id, "k": kind})


def read(settings: Settings, token: str) -> tuple[str, str] | None:
    try:
        data = _serializer(settings).loads(token)
    except BadSignature:
        return None
    return (data["u"], data["k"]) if isinstance(data, dict) and {"u", "k"} <= data.keys() else None
