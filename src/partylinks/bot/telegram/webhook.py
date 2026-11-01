"""POST /bot/telegram: Telegram posts every update here (a webhook, no polling). The bot starts with the
app when ``bot.enabled`` and both secrets are set, and registers this address with Telegram.

Every request must carry the webhook secret (X-Telegram-Bot-Api-Secret-Token): without it anyone could
post updates in a user's name. The route answers at once; the update is handled in the background, so a
slow search never makes Telegram retry it."""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from pydantic import SecretStr
from telegram import Update
from telegram.ext import Application

from partylinks.bot.telegram import handlers
from partylinks.config import Settings

log = logging.getLogger(__name__)
PATH = "/bot/telegram"
SECRET_HEADER = "x-telegram-bot-api-secret-token"
UPDATES = ["message", "callback_query", "inline_query"]
MIN_SECRET = 16  # characters; Telegram allows 1-256 of A-Z a-z 0-9 _ -


def strong(secret: SecretStr | None) -> bool:
    """Long enough not to be guessed (an empty one would let every request in)."""
    return secret is not None and len(secret.get_secret_value()) >= MIN_SECRET


def webhook_url(settings: Settings) -> str:
    return settings.bot.webhook_url or settings.site.base_url.rstrip("/") + PATH


async def start(app: FastAPI) -> Application | None:
    """The running bot, or None (off, not configured, or Telegram refused the token: logged)."""
    settings: Settings = app.state.settings
    cfg = settings.bot
    if not cfg.enabled or cfg.telegram_token is None:
        log.info("bot: Telegram off (bot.enabled false or no PARTYLINKS_BOT__TELEGRAM_TOKEN)")
        return None
    if not strong(cfg.telegram_webhook_secret):
        log.error(
            "bot: PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET is missing or shorter than %d characters: the Telegram bot stays off",
            MIN_SECRET,
        )
        return None
    bot = handlers.build(cfg.telegram_token.get_secret_value(), lambda: app.state.settings, app.state.db)
    try:
        await bot.initialize()
        await bot.start()
    except Exception:  # a wrong token, Telegram unreachable: the site runs regardless
        log.exception("bot: Telegram did not start")
        return None
    url = webhook_url(settings)
    if url.startswith("https://"):  # Telegram posts to https only; on localhost, set bot.webhook_url to a tunnel
        try:
            await bot.bot.set_webhook(
                url, secret_token=cfg.telegram_webhook_secret.get_secret_value(), allowed_updates=UPDATES,
                drop_pending_updates=False,
            )  # fmt: skip
            await handlers.describe(bot)
            log.info("bot: Telegram webhook at %s", url)
        except Exception:
            log.exception("bot: Telegram webhook not set")
    else:
        log.warning("bot: webhook %s is not https, Telegram will not post to it", url)
    return bot


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Runs inside the app's own lifespan (after its settings and database are up)."""
    bot = await start(app)
    app.state.telegram = bot
    try:
        yield
    finally:
        if bot is not None:
            try:
                await bot.stop()
                await bot.shutdown()
            except Exception:
                log.exception("bot: Telegram did not stop cleanly")


router = APIRouter(lifespan=lifespan)


@router.post(PATH, include_in_schema=False)
async def telegram_update(request: Request) -> Response:
    bot: Application | None = getattr(request.app.state, "telegram", None)
    settings: Settings = request.app.state.settings
    secret = settings.bot.telegram_webhook_secret
    if bot is None or secret is None or not strong(secret):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    given = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(given.encode(), secret.get_secret_value().encode()):
        # Telegram always sends the secret: a wrong one is a guess, and guesses add up to a ban
        # (the route is outside the per-IP rate limit, so this is what stops guessing).
        guard = getattr(request.app.state, "guard", None)
        if guard is not None and request.client:
            guard.strike(request.client.host, settings.security, "telegram webhook secret", time.monotonic())
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        data: Any = await request.json()
        update = Update.de_json(data, bot.bot)
    except (ValueError, TypeError, KeyError):  # not JSON, or not an update's shape
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not an update") from None
    if update is not None:
        await bot.update_queue.put(update)
    return Response(status_code=status.HTTP_200_OK)
