"""The worker's place-info job: every ``place_info.interval_hours`` (first soon after start)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

from partylinks.placeinfo.refresh import run_pass

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)


async def _run(scrape: ScrapeService) -> None:
    try:
        cfg = scrape.settings
        await asyncio.to_thread(run_pass, scrape.db, cfg.place_info, scrape._stopping, cfg.media.webp_quality)
    except Exception:
        log.exception("place info pass failed")


def schedule(scheduler: AsyncIOScheduler, settings: Settings, scrape: ScrapeService) -> None:
    """Called by ``scheduler.build_scheduler``."""
    if not settings.place_info.enabled:
        return
    scheduler.add_job(
        _run, IntervalTrigger(hours=settings.place_info.interval_hours, timezone=settings.timezone), args=(scrape,),
        id="place-info", name="place-info:ontopo-websites", max_instances=1, coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(minutes=5),
        misfire_grace_time=settings.scheduler.misfire_grace_seconds,
    )  # fmt: skip
