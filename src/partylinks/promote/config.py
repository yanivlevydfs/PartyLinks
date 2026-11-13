"""``promote:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromoteSettings(BaseModel):
    enabled: bool = True  # false: the form answers 404 (the page says so)
    max_events: int = Field(10, ge=1, le=50)  # events one request may name
    message_max: int = Field(2000, ge=100, le=10000)  # characters in the notes box
    phone_region: str = Field("IL", min_length=2, max_length=2)  # a number without +country is read as this country's
    email_admins: bool = True  # each new request is emailed to accounts.admin_emails (needs mail set up)
    admin_shown: int = Field(200, ge=10, le=2000)  # latest requests listed in the admin's tab
