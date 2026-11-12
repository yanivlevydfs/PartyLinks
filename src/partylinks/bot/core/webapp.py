"""🗺 SPOTX inside Telegram (a Mini App, no login): the private chat's menu button and a row in the 🏠 menu open
the site (``site.base_url``) with ``?src=tg-app``; the site then loads Telegram's script
(web/src/bot/telegramApp.ts). Telegram opens Web Apps over https only: on a plain-http base_url (localhost)
there is no row and the menu button stays the command list."""

from __future__ import annotations

from partylinks.bot.core.reply import Button
from partylinks.config import Settings

MENU_BUTTON = "🌐 SPOTX"  # the menu button beside the typing box: one text for every chat
TEXT = {"he": "🗺 פתיחת האתר", "en": "🗺 Open SPOTX"}


def url(cfg: Settings) -> str | None:
    """The app's address, None when the site is not on https."""
    base = cfg.site.base_url.rstrip("/")
    return f"{base}/?src=tg-app" if base.startswith("https://") else None


def rows(cfg: Settings, lang: str) -> list[list[Button]]:
    """The menu's row (none without https)."""
    link = url(cfg)
    return [[Button(TEXT[lang], url=link, web_app=True)]] if link else []
