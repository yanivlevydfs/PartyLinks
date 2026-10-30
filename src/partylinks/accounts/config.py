"""``accounts:`` in settings.yaml. Secrets come from the environment only (PARTYLINKS_ACCOUNTS__...)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class AccountsSettings(BaseModel):
    enabled: bool = True  # false: no account UI, /auth answers 404, the account jobs idle
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = None  # signs the sign-in state cookie and the unsubscribe links
    session_days: int = Field(30, ge=1, le=365)
    max_alerts: int = Field(20, ge=1)
    max_saved: int = Field(500, ge=1)
    # New bell notifications one user can get from one matcher pass (every 15 min): a flood guard.
    max_hits_per_pass: int = Field(30, ge=1)
    admin_emails: list[str] = Field(default_factory=list)  # promoted to admin on sign-in

    @property
    def ready(self) -> bool:
        """Sign-in works: switched on and every Google / session secret set."""
        return bool(self.enabled and self.google_client_id and self.google_client_secret and self.session_secret)
