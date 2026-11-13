"""``mail:`` in settings.yaml. The Resend key comes from the environment only (PARTYLINKS_MAIL__RESEND_API_KEY)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class MailSettings(BaseModel):
    resend_api_key: SecretStr | None = None  # missing: every email is off and the admin warns
    from_address: str = "SPOTX <events@spotx.fun>"
    digest_cron: str = "0 10 * * thu"  # in settings.timezone
    digest_max_events: int = Field(12, ge=1, le=30)
    instant_enabled: bool = False  # off: one email a week (the digest); alerts still fill the bell
    instant_min_interval_minutes: int = Field(60, ge=5)
    instant_max_age_minutes: int = Field(180, ge=5)  # older matches are never emailed, only shown in the bell
    matcher_interval_minutes: int = Field(15, ge=1)
    # The events of a digest / instant alert as one .ics attachment. Resend's batch endpoint takes no
    # attachments, so with this on those emails go one request each (2 a second), not 100 a request.
    calendar_attachment: bool = True
    # Signs new unsubscribe links (else accounts.session_secret). Links never expire, so when rotating a
    # secret keep the old one in unsubscribe_old_secrets: refusals must keep working from every email sent.
    unsubscribe_secret: SecretStr | None = None
    unsubscribe_old_secrets: list[SecretStr] = []
    instant_max_events: int = Field(10, ge=1, le=30)  # events in one instant alert email; the rest stay in the bell
    digest_days: int = Field(10, ge=1, le=31)  # the digest lists alert matches of events in the next this many days
    digest_saved_days: int = Field(7, ge=1, le=31)  # ...and the user's saved events in the next this many days
    digest_quiet_below: int = Field(4, ge=0, le=30)  # fewer events than this: the digest adds the weekend's picks
    digest_filler: int = Field(6, ge=0, le=20)  # events in each of those added sections (free, weekend highlights)
    welcome_delay_minutes: int = Field(2, ge=0, le=1440)  # the welcome email waits this long after sign-up
    welcome_batch: int = Field(500, ge=1, le=5000)  # welcome emails sent in one pass at most
    send_pause_seconds: float = Field(0.6, ge=0, le=10)  # between Resend requests: under its 2 a second
    image_quality: int = Field(82, ge=30, le=100)  # JPEG quality of the pictures in emails (new files only)
    image_cache_seconds: int = Field(604800, ge=0, le=31536000)  # browser / mail proxy cache of those pictures
    admin_runs_shown: int = Field(20, ge=1, le=500)  # recent email runs listed in the admin's email tab
