"""The worker's artist-links job: every ``artist_links.interval_minutes`` (first soon after start)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

from partylinks.artist_links.lookup import run_pass

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)


async def _run(scrape: ScrapeService) -> None:
    try:  # 1 request a second: on a thread; stops at the next name on shutdown
        await asyncio.to_thread(run_pass, scrape.db, scrape.settings.artist_links, scrape._stopping)
    except Exception:
        log.exception("artist links pass failed")


def schedule(scheduler: AsyncIOScheduler, settings: Settings, scrape: ScrapeService) -> None:
    """Called by ``scheduler.build_scheduler``."""
    if not settings.artist_links.enabled:
        return
    scheduler.add_job(
        _run, IntervalTrigger(minutes=settings.artist_links.interval_minutes, timezone=settings.timezone), args=(scrape,),
        id="artist-links", name="artist-links:musicbrainz", max_instances=1, coalesce=True,
        next_run_time=datetime.now(datetime.now().astimezone().tzinfo) + timedelta(minutes=5),
        misfire_grace_time=settings.scheduler.misfire_grace_seconds,
    )  # fmt: skip
