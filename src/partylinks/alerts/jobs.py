"""The worker's account jobs: match new events against alerts (then the email jobs, see partylinks.mail)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

from partylinks.alerts import matcher

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)
MATCH_JOB_ID = "alerts:match"


async def _match(service: ScrapeService) -> None:
    try:
        await asyncio.to_thread(matcher.run, service.settings, service.db)
    except Exception:
        log.exception("alert matching failed")


def schedule(scheduler: AsyncIOScheduler, settings: Settings, service: ScrapeService) -> None:
    """Called by ``scheduler.build_scheduler``; nothing when accounts are off."""
    if not settings.accounts.enabled:
        return
    scheduler.add_job(
        _match,
        IntervalTrigger(minutes=settings.mail.matcher_interval_minutes, timezone=settings.timezone),
        args=(service,),
        id=MATCH_JOB_ID,
        name="alerts:match",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=settings.scheduler.misfire_grace_seconds,
    )
