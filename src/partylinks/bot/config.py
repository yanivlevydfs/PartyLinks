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
    banner_url: str | None = None  # the banner's address, {lang} for he / en; empty: the site's /bot-banner-{lang}.png
    evening_tries: int = Field(10, ge=1, le=50)  # 🌙 plan my evening: events tried for one with places around it
    daily_hour: int = Field(10, ge=0, le=23)  # ☀️ /daily (opt-in): tonight's picks at this hour, site time zone
    daily_results: int = Field(3, ge=1, le=10)
    group_results: int = Field(3, ge=1, le=9)  # 👥 /find in a group: cards, then a poll (+ "none": Telegram's 10 options)
    # Housekeeping and timings (core/alerts.py, channel.py, digest.py, remind.py, picks.py).
    alert_stale_hours: int = Field(24, ge=1, le=720)  # an alert match not sent by then is dropped, never sent late
    channel_keep_days: int = Field(60, ge=7, le=365)  # channel posts remembered this long, so none is posted twice
    digest_again_days: int = Field(6, ge=1, le=30)  # a chat gets the digest at most once in this many days
    reminder_min_ahead_minutes: int = Field(5, ge=0, le=180)  # a reminder this close to its time is not set
    picks_clicks_days: int = Field(7, ge=1, le=90)  # digest / channel / daily picks rank by ticket clicks of these days
    picks_overshoot: int = Field(4, ge=1, le=20)  # rows read per pick, room for repeat dates of one show
    # Cards and buttons.
    evening_from_hour: int = Field(17, ge=0, le=23)  # a start today from this hour reads "tonight", earlier "today"
    follow_artists: int = Field(3, ge=0, le=10)  # ⭐ follow buttons for the lineup's first artists (plus the venue)
    title_in_text: int = Field(80, ge=10, le=300)  # an event name in a message or button is cut past this
    card_title_max: int = Field(300, ge=20, le=900)  # a card's name is cut past this (a caption holds 1024)
    # Telegram (telegram/handlers.py, webhook.py).
    inline_cache_seconds: int = Field(60, ge=0, le=3600)  # Telegram caches an "@bot ..." answer this long
    concurrent_updates: int = Field(16, ge=1, le=256)  # updates handled at once; read when the bot starts
    start_attempts: int = Field(4, ge=1, le=20)  # tries to reach Telegram at startup before the bot stays off
    start_pause_seconds: float = Field(3.0, ge=0, le=60)  # pause between those tries, times the try number
    # Words the bot understands (core/understand.py), casefolded, no punctuation.
    # Whole messages the bot answers itself.
    greeting_words: list[str] = Field(default_factory=lambda: [
        "היי", "הי", "שלום", "אהלן", "הלו", "היוש", "מה נשמע", "מה קורה", "מה המצב", "בוקר טוב", "ערב טוב",
        "hi", "hello", "hey", "yo", "sup", "hiya", "good morning", "good evening",
    ])  # fmt: skip
    thanks_words: list[str] = Field(default_factory=lambda: [
        "תודה", "תודה רבה", "תנקס", "אחלה", "מעולה", "סבבה", "יופי", "thanks", "thank you", "thx", "great", "cool",
    ])  # fmt: skip
    help_words: list[str] = Field(default_factory=lambda: [
        "עזרה", "help", "מה אתה יודע", "מה אתה יודע לעשות", "מה אפשר", "איך זה עובד", "מה אפשר לשאול",
    ])  # fmt: skip
    more_words: list[str] = Field(default_factory=lambda: [
        "עוד", "עוד אירועים", "תראה עוד", "תן עוד", "הבאים", "more", "next", "show more", "more please",
    ])  # fmt: skip
    alerts_words: list[str] = Field(default_factory=lambda: ["התראות", "ההתראות שלי", "ההתראות", "my alerts", "alerts"])
    # Inside a search: options of the bot, not words to find.
    near_words: list[str] = Field(default_factory=lambda: [
        "קרוב אליי", "קרוב אלי", "קרוב לידי", "לידי", "בסביבה", "בסביבתי", "באזור שלי",
        "near me", "nearby", "around me", "close to me",
    ])  # fmt: skip
    cheap_words: list[str] = Field(default_factory=lambda: [
        "הכי זול", "הכי זולים", "הכי זולות", "זולים", "זולות", "זול", "בזול", "cheapest", "cheap",
    ])  # fmt: skip
    # A follow-up's opening ("and what about haifa"), tried in this order; keep the trailing space.
    follow_markers: list[str] = Field(default_factory=lambda: [
        "ומה עם ", "ומה לגבי ", "מה עם ", "מה לגבי ", "and what about ", "what about ", "how about ", "and ",
    ])  # fmt: skip
