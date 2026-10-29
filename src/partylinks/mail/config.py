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
