"""The admin's bot tab: ``GET /admin/api/bot`` (usage counts, what is set up). Admin only (``AdminDep``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from partylinks.admin import AdminDep, SessionDep
from partylinks.bot.core import usage
from partylinks.bot.telegram.webhook import MIN_SECRET, webhook_url

router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep])


@router.get("/bot")
def bot_stats(request: Request, session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    settings = request.app.state.settings
    cfg = settings.bot
    secret = cfg.telegram_webhook_secret
    return usage.stats(session, settings.timezone, days) | {
        "setup": {
            "enabled": cfg.enabled,
            "token": cfg.telegram_token is not None,
            "secret": secret is not None and len(secret.get_secret_value()) >= MIN_SECRET,
            "running": getattr(request.app.state, "telegram", None) is not None,
            "webhook": webhook_url(settings),
            "channel": cfg.channel_id,
            "digest": {"weekday": cfg.digest_weekday, "hour": cfg.digest_hour},
        }
    }
