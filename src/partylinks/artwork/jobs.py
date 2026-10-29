"""The worker's artwork job: every 15 minutes (and soon after start), cards for events with no picture."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

from partylinks.artwork import service

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)
INTERVAL_MINUTES = 15


async def _run(scrape: ScrapeService) -> None:
    try:
        await service.run_async(scrape.settings, scrape.db)  # draws on threads, saves each card in its own transaction
    except Exception:
        log.exception("artwork pass failed")


def schedule(scheduler: AsyncIOScheduler, settings: Settings, scrape: ScrapeService) -> None:
    """Called by ``scheduler.build_scheduler``. First run two minutes after start, then every 15 minutes."""
    scheduler.add_job(
        _run, IntervalTrigger(minutes=INTERVAL_MINUTES, timezone=settings.timezone), args=(scrape,),
        id="artwork:draw", name="artwork:draw", max_instances=1, coalesce=True,
        next_run_time=datetime.now(datetime.now().astimezone().tzinfo) + timedelta(minutes=2),
        misfire_grace_time=settings.scheduler.misfire_grace_seconds,
    )  # fmt: skip
