"""Sending through Resend's batch API: 100 messages a request, an idempotency key per request, and at
most two requests a second (Resend's default limit)."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import resend
from resend.exceptions import ResendError

from partylinks.accounts.redact import scrub
from partylinks.config import Settings

log = logging.getLogger(__name__)
BATCH = 100
PAUSE = 0.6  # seconds between requests: under 2 a second
# Our account's problems (billing, quota, key), not the message's: the run stops, nobody else can fix it.
ACCOUNT_CODES = {401, 402, 403, 429}
ACCOUNT_TYPES = {
    "missing_api_key",
    "invalid_api_key",
    "restricted_api_key",
    "daily_quota_exceeded",
    "rate_limit_exceeded",
}


class MailAccountError(Exception):
    pass


@dataclass
class Message:
    user_id: str  # logs and keys carry this, never the address
    to: str
    subject: str
    html: str
    text: str
    unsubscribe_url: str
    reply_to: str = ""
    tags: dict[str, str] = field(default_factory=dict)


def ready(settings: Settings) -> bool:
    return settings.mail.resend_api_key is not None and settings.accounts.session_secret is not None


def _account_error(exc: ResendError) -> bool:
    try:
        code = int(exc.code)
    except (TypeError, ValueError):
        code = 0
    return code in ACCOUNT_CODES or exc.error_type in ACCOUNT_TYPES


def _params(settings: Settings, m: Message) -> dict[str, Any]:
    params: dict[str, Any] = {
        "from": settings.mail.from_address,
        "to": [m.to],
        "subject": m.subject,
        "html": m.html,
        "text": m.text,
        # RFC 8058 one-click unsubscribe: Gmail and others show "Unsubscribe" next to the sender.
        "headers": {
            "List-Unsubscribe": f"<{m.unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        "tags": [{"name": k, "value": v} for k, v in m.tags.items()],
    }
    if m.reply_to:
        params["reply_to"] = m.reply_to
    return params


def send(settings: Settings, messages: list[Message], key: str) -> tuple[list[Message], list[Message], str | None]:
    """Send ``messages``; ``key`` names the run (kind and period) for the idempotency keys.
    Returns (sent, failed, first error). Raises MailAccountError on an account problem."""
    assert settings.mail.resend_api_key is not None
    resend.api_key = settings.mail.resend_api_key.get_secret_value()
    sent: list[Message] = []
    failed: list[Message] = []
    error: str | None = None
    for start in range(0, len(messages), BATCH):
        chunk = messages[start : start + BATCH]
        users = ",".join(m.user_id for m in chunk)
        idem = f"{key}:{hashlib.sha256(users.encode()).hexdigest()[:32]}"  # the same chunk again: sent once
        try:
            resend.Batch.send([_params(settings, m) for m in chunk], {"idempotency_key": idem})  # type: ignore[arg-type,typeddict-item]
            sent += chunk
        except ResendError as exc:
            message = scrub(f"{exc.code} {exc.error_type}: {exc.message}")
            if _account_error(exc):
                log.warning("mail %s stopped: Resend account problem: %s", key, message)
                raise MailAccountError(message) from exc
            log.error("mail %s: a batch of %d failed: %s", key, len(chunk), message)
            failed += chunk
            error = error or message
        if start + BATCH < len(messages):
            time.sleep(PAUSE)
    return sent, failed, error
