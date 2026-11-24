"""``community:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides). The CAPTCHA's
secret comes from the environment only (PARTYLINKS_COMMUNITY__TURNSTILE_SECRET)."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class CommunitySettings(BaseModel):
    enabled: bool = True  # false: the form answers 404 (the page says so)
    # Cloudflare Turnstile. Without the secret, posting answers 503 unless captcha_required is off (tests, dev).
    captcha_required: bool = True
    turnstile_site_key: str = ""  # public: the page's widget
    turnstile_secret: SecretStr | None = None
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    # An account's events go live without review once this many of its events were approved by an admin.
    trusted_after: int = Field(1, ge=0, le=100)  # 0: every event goes live at once
    max_per_day: int = Field(5, ge=1, le=100)  # events one account may post in 24 hours
    max_days_ahead: int = Field(365, ge=1, le=1000)  # how far ahead an event may start
    title_max: int = Field(140, ge=20, le=300)
    description_min: int = Field(20, ge=0, le=500)
    description_max: int = Field(6000, ge=200, le=20000)
    max_tickets: int = Field(8, ge=1, le=30)  # ticket links (each one a tier: name, link, price)
    max_images: int = Field(5, ge=1, le=10)  # the first is the main picture
    max_artists: int = Field(20, ge=1, le=60)
    max_genres: int = Field(5, ge=1, le=20)
    max_event_types: int = Field(3, ge=1, le=10)
    # The form's "what kind of event" choices; enrichment reads them as a source's own categories.
    event_types: list[str] = Field(
        default_factory=lambda: [
            "מסיבה",
            "מועדון",
            "הופעה",
            "פסטיבל",
            "סטנדאפ",
            "הצגה",
            "הרצאה",
            "סדנה",
            "ילדים",
            "אחר",
        ]
    )
    # Pictures: the file sent, before conversion; smaller ones are refused (a blurry card).
    upload_max_bytes: int = Field(10_000_000, ge=100_000, le=50_000_000)
    upload_min_side: int = Field(300, ge=50, le=2000)
    uploads_per_day: int = Field(40, ge=1, le=500)  # pictures one account may send in 24 hours
    unused_upload_hours: float = Field(48, gt=0)  # a picture no posted event uses is deleted after this
    # Ticket links: https only; never a link shortener (it hides where it goes) nor these hosts.
    blocked_link_hosts: list[str] = Field(
        default_factory=lambda: ["bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly",
                                 "rebrand.ly", "shorturl.at", "tiny.cc", "rb.gy", "s.id"]
    )  # fmt: skip
    sponsor_note_max: int = Field(1000, ge=100, le=5000)
    reject_reason_max: int = Field(500, ge=50, le=2000)
    admin_shown: int = Field(200, ge=10, le=2000)  # latest events listed in the admin's tab
