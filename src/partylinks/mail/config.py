"""``mail:`` in settings.yaml. The Resend key comes from the environment only (PARTYLINKS_MAIL__RESEND_API_KEY)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class MailSettings(BaseModel):
    resend_api_key: SecretStr | None = None  # missing: every email is off and the admin warns
    from_address: str = "SPOTX <events@spotx.fun>"
    digest_cron: str = "0 10 * * thu"  # in settings.timezone
    digest_max_events: int = Field(12, ge=1, le=30)
    instant_min_interval_minutes: int = Field(60, ge=5)
    matcher_interval_minutes: int = Field(15, ge=1)
    # Signs new unsubscribe links (else accounts.session_secret). Links never expire, so when rotating a
    # secret keep the old one in unsubscribe_old_secrets: refusals must keep working from every email sent.
    unsubscribe_secret: SecretStr | None = None
    unsubscribe_old_secrets: list[SecretStr] = []
