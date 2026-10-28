"""Server and app status in the log: a block when the server starts, a one-line status every
``logging.status_every_minutes`` while it runs, and a line when it stops.

INFO level, so it shows in the console by default (and in Railway's log); debug mode adds the
detailed lines on top. Secrets never appear: the proxy and the database URL show without passwords.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from partylinks.config import Settings
from partylinks.db import ClickRow, Database, EventRow, ScrapeRunRow, upcoming_filter
from partylinks.scheduler import next_run_times
from partylinks.scrapers import configured_sources, source_errors

log = logging.getLogger("partylinks.status")
VERSION = "0.1.0"
_started = time.monotonic()


def _mb(n: int | None) -> str:
    return "n/a" if n is None else f"{n / 1_000_000:.1f} MB"


def _uptime() -> str:
    minutes = int(time.monotonic() - _started) // 60
    return (
        f"{minutes // 1440}d {minutes // 60 % 24}h {minutes % 60}m"
        if minutes >= 1440
        else f"{minutes // 60}h {minutes % 60}m"
    )


def _counts(db: Database) -> dict[str, Any]:
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    with db.session(write=False) as s:
        per_status = select(ScrapeRunRow.status, func.count()).where(ScrapeRunRow.started_at >= day_ago)
        runs = {state: n for state, n in s.execute(per_status.group_by(ScrapeRunRow.status))}
        return {
            "events": s.scalar(select(func.count()).select_from(EventRow)) or 0,
            "upcoming": s.scalar(select(func.count()).select_from(EventRow).where(upcoming_filter())) or 0,
            "runs": runs,
            "clicks": s.scalar(select(func.count()).select_from(ClickRow).where(ClickRow.at >= day_ago)) or 0,
        }


def _next_job(scheduler: AsyncIOScheduler, tz: str) -> str:
    upcoming = [(t, job) for job, t in next_run_times(scheduler).items() if t]
    if not upcoming:
        return "none"
    when, job = min(upcoming)
    return f"{job} at {when.astimezone(ZoneInfo(tz)):%H:%M}"


def log_startup(cfg: Settings, db: Database, scheduler: AsyncIOScheduler | None) -> None:
    sources = configured_sources(cfg)
    enabled = sum(1 for s in sources.values() if s.enabled)
    counts = _counts(db)
    storage = db.storage_stats()
    proxy = cfg.http.source_proxy or cfg.http.proxy
    proxies = [p.strip().rpartition("@")[2] for p in (proxy or "").split(",") if p.strip()]
    commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7]
    where = (
        f"Railway ({os.environ['RAILWAY_ENVIRONMENT_NAME']})" if os.environ.get("RAILWAY_ENVIRONMENT_NAME") else "local"
    )
    lines = [
        f"===== {cfg.site.name} {VERSION}{f' ({commit})' if commit else ''} starting =====",
        f"server     http://{cfg.api.host}:{cfg.api.port}  public {cfg.site.base_url}  [{where}]",
        f"runtime    Python {platform.python_version()} on {platform.system()}  pid {os.getpid()}  tz {cfg.timezone}",
        f"database   {make_url(cfg.database.url).render_as_string(hide_password=True)}  {_mb(storage['database_bytes'])}",
        f"events     {counts['upcoming']} upcoming / {counts['events']} stored",
        f"scheduler  {'on: ' + str(len(scheduler.get_jobs())) + ' jobs, next ' + _next_job(scheduler, cfg.timezone) if scheduler else 'OFF (no scraping here)'}",
        f"sources    {enabled} enabled / {len(sources)}  config errors: {len(source_errors(cfg))}",
        f"proxy      {', '.join(proxies) if proxies else 'none (direct connection)'}",
        (
            f"admin      {'token set' if cfg.admin.token else 'no token: localhost only'}  "
            f"rate limit {cfg.security.rate_limit_per_minute}/min  allowed hosts: {len(cfg.security.allowed_hosts) or 'any'}"
        ),
        (
            f"logging    {cfg.logging.level}{' + DEBUG' if cfg.logging.debug else ''}  {cfg.logging.format}  "
            f"file {cfg.logging.file or 'none'}  status every {cfg.logging.status_every_minutes or '-'} min"
        ),
        "=" * 40,
    ]
    for line in lines:
        log.info(line)


def log_status(cfg: Settings, db: Database, scheduler: AsyncIOScheduler | None) -> None:
    c = _counts(db)
    runs = ", ".join(f"{n} {k}" for k, n in sorted(c["runs"].items())) or "none"
    state = "off" if scheduler is None else ("running" if scheduler.running else "STOPPED")
    nxt = f", next {_next_job(scheduler, cfg.timezone)}" if scheduler else ""
    log.info(
        "status: up %s | events %d upcoming / %d | runs 24h: %s | scheduler %s%s | clicks 24h: %d",
        _uptime(), c["upcoming"], c["events"], runs, state, nxt, c["clicks"],
    )  # fmt: skip


async def status_loop(cfg: Settings, db: Database, scheduler: AsyncIOScheduler | None) -> None:
    """``log_status`` every ``status_every_minutes`` until cancelled; a failure never stops it."""
    while True:
        await asyncio.sleep(cfg.logging.status_every_minutes * 60)
        try:
            await asyncio.to_thread(log_status, cfg, db, scheduler)
        except Exception as exc:  # noqa: BLE001 - the status line must never take the server down
            log.warning("status: %s", exc)


def log_shutdown(cfg: Settings) -> None:
    log.info("===== %s stopping after %s =====", cfg.site.name, _uptime())
