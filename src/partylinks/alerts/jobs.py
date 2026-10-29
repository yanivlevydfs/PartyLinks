"""The worker's account jobs: match new events against alerts, then email them (partylinks.mail.jobs):
instant after each pass, welcome every 5 minutes, the digest on ``mail.digest_cron``."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from partylinks.alerts import matcher
from partylinks.mail import jobs as mail

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)
MATCH_JOB_ID = "alerts:match"


async def _match(service: ScrapeService) -> None:
    """Match, then email the new matches of alerts set to "email me at once"."""
    try:
        await asyncio.to_thread(matcher.run, service.settings, service.db)
    except Exception:
        log.exception("alert matching failed")
    await _mail(mail.instant, service)


async def _mail(job: Any, service: ScrapeService) -> None:
    try:
        await asyncio.to_thread(job, service.settings, service.db)
    except Exception:
        log.exception("mail %s failed", job.__name__)


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
    scheduler.add_job(
        _mail, IntervalTrigger(minutes=5, timezone=settings.timezone), args=(mail.welcome, service),
        id="mail:welcome", name="mail:welcome", max_instances=1, coalesce=True,
        misfire_grace_time=settings.scheduler.misfire_grace_seconds,
    )  # fmt: skip
    scheduler.add_job(
        _mail, CronTrigger.from_crontab(settings.mail.digest_cron, timezone=settings.timezone), args=(mail.digest, service),
        id="mail:digest", name="mail:digest", max_instances=1, coalesce=True,
        misfire_grace_time=6 * 3600,  # a deploy at 10:00 still sends Thursday's digest
    )  # fmt: skip
