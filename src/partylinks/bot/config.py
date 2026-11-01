"""``bot:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides).
Secrets from the environment only: PARTYLINKS_BOT__TELEGRAM_TOKEN, PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class BotSettings(BaseModel):
    enabled: bool = True  # off: no webhook, no alert job, the route answers 404
    telegram_token: SecretStr | None = None  # from @BotFather; missing: the bot is off
    # Telegram sends it in X-Telegram-Bot-Api-Secret-Token on every update (1-256 of A-Z a-z 0-9 _ -).
    telegram_webhook_secret: SecretStr | None = None
    webhook_url: str | None = None  # default: site.base_url + /bot/telegram
    results: int = Field(5, ge=1, le=10)  # event cards per answer
    inline_results: int = Field(10, ge=1, le=50)  # cards offered by "@bot ..." in another chat
    max_text_length: int = Field(200, ge=20, le=1000)  # longer messages are cut
    messages_per_minute: int = Field(20, ge=1)  # per chat, then a gentle "wait"
    inline_per_minute: int = Field(60, ge=1)  # "@bot ..." queries per user (Telegram asks on every keystroke)
    memory_minutes: int = Field(60, ge=1)  # "and tomorrow?" continues a search this recent
    near_km: float = Field(10, gt=0, le=200)  # "near me": events within this distance
    max_alerts: int = Field(10, ge=1, le=50)  # alerts per chat
    alert_interval_minutes: int = Field(30, ge=5)  # how often new events are matched and sent
    alert_max_events: int = Field(5, ge=1, le=10)  # cards in one alert message (the soonest)
    alert_max_per_day: int = Field(10, ge=1)  # alert cards per chat in any 24 hours
    # Phase 1b: ⏰ remind me, 👁 watch, ⭐ follow.
    reminder_hours: list[float] = Field(default_factory=lambda: [24.0, 2.0])  # before the start, each one message
    max_reminders: int = Field(30, ge=1, le=200)  # events with a reminder, per chat
    max_watches: int = Field(20, ge=1, le=200)  # watched events, per chat
    watch_min_drop_percent: float = Field(5, ge=0, le=90)  # a smaller price drop is no news
    # Phase 1c: 🔁 similar, the weekly digest.
    similar_results: int = Field(5, ge=1, le=10)
    digest_weekday: int = Field(3, ge=0, le=6)  # 0 Monday .. 6 Sunday (3: Thursday), in the site's time zone
    digest_hour: int = Field(12, ge=0, le=23)
    digest_results: int = Field(5, ge=1, le=10)
    # Phase 2: a Telegram channel the bot posts picks to (the bot must be its admin). Empty: no posts.
    channel_id: str | None = None  # "@spotx_events" or "-100..."
    channel_hour: int = Field(16, ge=0, le=23)  # one post a day: tonight's picks (Thursday: the weekend's)
    channel_results: int = Field(5, ge=1, le=10)
    usage_days: int = Field(180, ge=7)  # usage rows kept for the admin's bot tab
    # The entry screen and the menu (consent.py, menu.py).
    inactive_days: int = Field(365, ge=30)  # a chat silent this long is deleted, alerts with it (the entry screen says so)
    banner_url: str | None = None  # the entry screen's picture; empty: the site's /og-default.png
