"""One independent cron job per enabled source."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from partylinks import indexnow
from partylinks.config import Settings
from partylinks.scrapers import configured_sources
from partylinks.service import ScrapeService, SourceBusyError

log = logging.getLogger(__name__)

RETENTION_JOB_ID = "retention"
STARTUP_STAGGER = timedelta(minutes=3)


async def _scheduled_run(service: ScrapeService, source: str) -> None:
    try:
        await service.run_source(source, trigger="schedule")
    except SourceBusyError:
        log.info("skipping scheduled %s run: previous run still in progress", source)
    except asyncio.CancelledError:
        log.info("scheduled %s run stopped: server shutting down (recorded as interrupted)", source)
    except Exception:
        log.exception("scheduled run of %s failed before it could be recorded", source)


async def _scheduled_retention(service: ScrapeService) -> None:
    try:
        await service.purge_expired()
    except SourceBusyError:
        log.info("skipping scheduled retention: already running")
    except Exception:
        log.exception("scheduled retention failed")


async def _scheduled_indexnow(service: ScrapeService) -> None:
    try:
        await asyncio.to_thread(indexnow.submit, service.settings, service.db)
    except Exception:
        log.exception("indexnow submit failed")


def build_scheduler(settings: Settings, service: ScrapeService) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    now = datetime.now(ZoneInfo(settings.timezone))
    enabled = [(n, s) for n, s in configured_sources(settings).items() if s.enabled]
    for index, (name, source) in enumerate(enabled):
        # Startup runs are spread out so a deploy never fires every source at once.
        # APScheduler treats an explicit next_run_time=None as "paused", so only pass it when set.
        startup = {"next_run_time": now + index * STARTUP_STAGGER} if settings.scheduler.run_on_startup else {}
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
    if settings.site.indexnow.key:
        scheduler.add_job(
            _scheduled_indexnow,
            CronTrigger.from_crontab(settings.site.indexnow.cron, timezone=settings.timezone),
            args=(service,),
            id="indexnow",
            name="indexnow:submit",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=settings.scheduler.misfire_grace_seconds,
        )
    from partylinks.alerts.jobs import schedule as schedule_alerts  # local: alerts imports the api

    schedule_alerts(scheduler, settings, service)  # alert matching (and the email jobs)
    return scheduler


def next_run_times(scheduler: AsyncIOScheduler | None) -> dict[str, datetime | None]:
    if scheduler is None:
        return {}
    # A job added before the scheduler starts has no next_run_time attribute yet.
    return {job.id: getattr(job, "next_run_time", None) for job in scheduler.get_jobs()}
