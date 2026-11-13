"""``accounts:`` in settings.yaml. Secrets come from the environment only (PARTYLINKS_ACCOUNTS__...)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class RateLimitSettings(BaseModel):
    """Calls a minute per IP on the account routes (accounts.guard.limited); over it answers 429."""

    auth: int = Field(10, ge=1, le=1000)  # sign-in with Google, Facebook or a passkey
    me: int = Field(30, ge=1, le=1000)  # a signed-in user's writes: alerts, saved, account, plans' groups
    alert_preview: int = Field(60, ge=1, le=1000)  # the alert form's live count (counted in the "me" bucket)
    passkey: int = Field(20, ge=1, le=1000)  # adding / removing one's own passkeys
    plans: int = Field(20, ge=1, le=1000)  # creating a plan and adding an event to compare
    plan_votes: int = Field(60, ge=1, le=1000)  # answers on a plan
    fb_deletion: int = Field(30, ge=1, le=1000)  # Meta's data-deletion callback


class AccountsSettings(BaseModel):
    enabled: bool = True  # false: no account UI, /auth answers 404, the account jobs idle
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    facebook_app_id: str = ""
    facebook_app_secret: SecretStr | None = None
    session_secret: SecretStr | None = None  # signs the sign-in state cookie and the unsubscribe links
    session_days: int = Field(30, ge=1, le=365)
    max_alerts: int = Field(20, ge=1)
    max_saved: int = Field(500, ge=1)
    # New bell notifications one user can get from one matcher pass (every 15 min): a flood guard.
    max_hits_per_pass: int = Field(30, ge=1)
    # ...and from all passes of the last 24 hours: a source back after days of failures lists its backlog at once.
    max_hits_per_day: int = Field(10, ge=1)
    admin_emails: list[str] = Field(default_factory=list)  # promoted to admin on sign-in
    session_roll_hours: int = Field(24, ge=1, le=24 * 30)  # a session's expiry moves forward at most this often
    sign_in_minutes: int = Field(10, ge=1, le=60)  # how long one Google / Facebook sign-in may take (restart to apply)
    admin_users_page: int = Field(25, ge=5, le=500)  # users per page in the admin's users tab
    rate_limits: RateLimitSettings = Field(default_factory=RateLimitSettings)

    @property
    def google_ready(self) -> bool:
        return bool(self.enabled and self.session_secret and self.google_client_id and self.google_client_secret)

    @property
    def facebook_ready(self) -> bool:
        return bool(self.enabled and self.session_secret and self.facebook_app_id and self.facebook_app_secret)

    @property
    def ready(self) -> bool:
        """Sign-in works: switched on, the session secret set, and Google or Facebook fully set."""
        return self.google_ready or self.facebook_ready

    @property
    def providers(self) -> list[str]:
        """The sign-in buttons the site shows, in order."""
        return [name for name, on in (("google", self.google_ready), ("facebook", self.facebook_ready)) if on]
