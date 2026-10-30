"""Unsubscribe links: {user id, kind}, signed. They never expire: the law wants a refusal to keep working
from any email ever sent, so a rotated secret stays valid for reading: verification tries
``mail.unsubscribe_old_secrets``, ``accounts.session_secret`` and ``mail.unsubscribe_secret``; new links are
signed with ``mail.unsubscribe_secret`` when set, else ``accounts.session_secret``."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from partylinks.config import Settings


def _serializer(settings: Settings) -> URLSafeSerializer:
    mail, session = settings.mail, settings.accounts.session_secret
    # itsdangerous verifies with every key and signs with the last one.
    keys = [s.get_secret_value() for s in (*mail.unsubscribe_old_secrets, session, mail.unsubscribe_secret) if s]
    if not keys:
        raise RuntimeError("neither mail.unsubscribe_secret nor accounts.session_secret is set")
    return URLSafeSerializer(list(dict.fromkeys(keys)), salt="unsubscribe")


def available(settings: Settings) -> bool:
    return bool(settings.mail.unsubscribe_secret or settings.accounts.session_secret)


def make(settings: Settings, user_id: str, kind: str) -> str:
    return _serializer(settings).dumps({"u": user_id, "k": kind})


def read(settings: Settings, token: str) -> tuple[str, str] | None:
    if not available(settings):
        return None
    try:
        data = _serializer(settings).loads(token)
    except BadSignature:
        return None
    return (data["u"], data["k"]) if isinstance(data, dict) and {"u", "k"} <= data.keys() else None
