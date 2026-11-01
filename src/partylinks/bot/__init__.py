"""The SPOTX chat bot (Telegram first; see docs/telegram-bot-design.md and docs/bot.md).

People write what they look for ("טכנו בתל אביב בסופ״ש", "jazz tonight", an artist, a venue) and get
event cards with ticket links. Search is the site's own (``search.smart_filters`` through
``api.build_event_filters``), so the bot finds exactly what the site finds.

- ``config``: the ``bot:`` settings block (the token and webhook secret come from the environment).
- ``models``: ``bot_chats``, ``bot_alerts``, ``bot_alert_hits``, ``bot_state``, ``bot_refs``,
  ``bot_reminders``, ``bot_watches``, ``bot_channel_posts``, ``bot_usage``.
- ``admin_routes``: the admin's bot tab (``GET /admin/api/bot``).
- ``core``: the conversation, knowing nothing about Telegram (a WhatsApp adapter can reuse it).
- ``telegram``: the Telegram adapter: webhook route, handlers, the alert job's sender.

This module stays light: ``partylinks.config`` imports ``bot.config`` through it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def install(app: FastAPI) -> None:
    """The webhook route and the admin's bot tab; the bot itself starts with the app (``telegram.webhook``),
    only when configured."""
    from partylinks.bot.admin_routes import router as admin_router
    from partylinks.bot.telegram.webhook import router

    app.include_router(router)
    app.include_router(admin_router)
