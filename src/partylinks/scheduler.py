"""One independent cron job per enabled source."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from partylinks.config import Settings
from partylinks.scrapers import configured_sources
from partylinks.service import ScrapeService, SourceBusyError

log = logging.getLogger(__name__)

RETENTION_JOB_ID = "retention"


async def _scheduled_run(service: ScrapeService, source: str) -> None:
    try:
        await service.run_source(source, trigger="schedule")
    except SourceBusyError:
        log.info("skipping scheduled %s run: previous run still in progress", source)
    except Exception:  # noqa: BLE001 - keep the job scheduled; the next run tries again
        log.exception("scheduled run of %s failed before it could be recorded", source)


async def _scheduled_retention(service: ScrapeService) -> None:
    try:
        await service.purge_expired()
    except SourceBusyError:
        log.info("skipping scheduled retention: already running")
    except Exception:  # noqa: BLE001 - outcome is kept in service.retention_state for /health
        log.exception("scheduled retention failed")


def build_scheduler(settings: Settings, service: ScrapeService) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    # APScheduler treats an explicit next_run_time=None as "paused", so only pass it when set.
    startup = {"next_run_time": datetime.now(ZoneInfo(settings.timezone))} if settings.scheduler.run_on_startup else {}
    for name, source in configured_sources(settings).items():
        if not source.enabled:
            continue
        try:  # one bad trigger skips only that source
            scheduler.add_job(
                _scheduled_run,
                CronTrigger.from_crontab(source.cron, timezone=settings.timezone),
                args=(service, name),
                id=name,
                name=f"scrape:{name}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=settings.scheduler.misfire_grace_seconds,
                **startup,
            )
        except ValueError as exc:
            log.error("source %s not scheduled: %s", name, exc)
    if settings.retention.enabled:
        scheduler.add_job(
            _scheduled_retention,
            CronTrigger.from_crontab(settings.retention.cron, timezone=settings.timezone),
            args=(service,),
            id=RETENTION_JOB_ID,
            name="retention:purge-expired",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=settings.scheduler.misfire_grace_seconds,
        )
    return scheduler


def next_run_times(scheduler: AsyncIOScheduler | None) -> dict[str, datetime | None]:
    if scheduler is None:
        return {}
    return {job.id: job.next_run_time for job in scheduler.get_jobs()}
