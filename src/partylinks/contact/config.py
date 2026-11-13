"""``contact:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContactSettings(BaseModel):
    enabled: bool = True  # false: the form answers 404 and the page shows only the email address
    message_max: int = Field(3000, ge=100, le=10000)  # characters in the message box
    admin_shown: int = Field(200, ge=10, le=2000)  # latest messages listed in the admin's requests tab
