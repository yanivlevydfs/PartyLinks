"""The worker process (``partylinks worker``): the scheduler, every source, retention and the startup
passes, away from the web process. Every ``scheduler.heartbeat_seconds`` it runs the admin's commands
and publishes its state; neither write counts as a data change (they go through the engine, not a
session), so the web's cached pages only go stale when the worker saves real data."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime, timedelta
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from partylinks.config import Settings, get_settings
from partylinks.db import Database, utcnow
from partylinks.scheduler import build_scheduler
from partylinks.scrapers import configured_sources
from partylinks.service import RunResult, ScrapeService, SourceBusyError
from partylinks.worker.models import STATE_ID, WorkerCommandRow, WorkerStateRow

log = logging.getLogger(__name__)

KEEP_COMMANDS = timedelta(days=1)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Worker:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings, self.db = settings, db
        self.started_at = utcnow()
        # The worker owns the runs: a run still "running" from a worker that died is marked interrupted.
        self.service = ScrapeService(settings, db)
        self.service.recompute_areas()
        self.scheduler = build_scheduler(settings, self.service)
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):  # Windows: Ctrl+C still raises KeyboardInterrupt
                loop.add_signal_handler(sig, self._stop.set)
        await asyncio.to_thread(self._publish)
        self._spawn(self._startup())
        self._spawn(self._beat())
        log.info("worker: started (pid %d), %d scheduled jobs", os.getpid(), len(self.scheduler.get_jobs()))
        try:
            await self._stop.wait()
        finally:
            log.info("worker: stopping")
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
            for task in self._tasks:
                task.cancel()
            self.service.shutdown()
            with contextlib.suppress(Exception):  # the database may be the reason we stop
                await asyncio.to_thread(self._publish, "stopped")
            self.db.engine.dispose()

    def _spawn(self, job: Any) -> None:
        task = asyncio.create_task(job)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _startup(self) -> None:
        """Before any source writes: artwork still in the database moves to files and genres are re-read
        if their config changed; then the scrapers start, and map coordinates are filled."""
        for job in (self.service.media.move_to_files, self.service.recompute_genres):
            try:
                await asyncio.to_thread(job)
            except Exception:  # the scrapers run regardless
                log.exception("worker: startup step %s failed", getattr(job, "__name__", job))
        # WebP copies older files still lack: in the background, so the scrapers start now.
        self._spawn(asyncio.to_thread(self.service.media.build_missing_variants))
        self.scheduler.start()
        await asyncio.to_thread(self.service.geocode)

    async def _beat(self) -> None:
        """Run pending commands and publish the state, every heartbeat; a failure never stops the loop."""
        every = self.settings.scheduler.heartbeat_seconds
        beats = 0
        while True:
            try:
                for command in await asyncio.to_thread(self._pending):
                    result = self._execute(command.command, command.args or {})  # on the loop: scheduler calls
                    await asyncio.to_thread(self._finish, command.id, result)
                await asyncio.to_thread(self._publish)
                beats += 1
                if beats % 720 == 0:  # about hourly: answered commands older than a day go
                    await asyncio.to_thread(self._prune)
            except Exception:
                log.exception("worker: heartbeat failed")
            await asyncio.sleep(every)

    # ------------------------------------------------------------------ commands
    def _execute(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        log.info("worker: command %s %s", command, args or "")
        try:
            if command == "pause":
                self.scheduler.pause()
                return {"status": "pause"}
            if command == "resume":
                self.scheduler.resume()
                return {"status": "resume"}
            if command in ("pause_job", "resume_job"):
                job = (self.scheduler.pause_job if command == "pause_job" else self.scheduler.resume_job)(args["id"])
                return {"id": args["id"], "next_run_at": _iso(getattr(job, "next_run_time", None))}
            if command == "scan":
                return self._scan(args.get("sources") or [])
            if command == "reload":
                return self._reload()
        except JobLookupError:
            return {"error": f"no scheduled job {args.get('id')!r}"}
        except Exception as exc:  # reported to the admin, never kills the worker
            log.exception("worker: command %s failed", command)
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"error": f"unknown command {command!r}"}

    def _scan(self, sources: list[str]) -> dict[str, list[str]]:
        started, busy = [], []
        for name in sources:
            try:
                future = self.service.start_source(name, trigger="admin")
            except SourceBusyError:
                busy.append(name)
                continue
            started.append(name)
            future.add_done_callback(_report(name))
        return {"started": started, "busy": busy}

    def _reload(self) -> dict[str, Any]:
        """Settings re-read from disk and environment; the service and scheduler rebuilt with them."""
        old = self.service
        if running := [n for n in configured_sources(self.settings) if old.is_running(n)]:
            return {"applied": False, "reason": f"waiting for running sources: {', '.join(running)}; apply again later"}
        get_settings.cache_clear()
        self.settings = get_settings()
        self.service = ScrapeService(self.settings, self.db)
        self.service.recompute_areas()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.scheduler = build_scheduler(self.settings, self.service)
        self.scheduler.start()
        old.shutdown()
        return {"applied": True}

    # ------------------------------------------------------------------ the two tables
    def _pending(self) -> list[Any]:
        with self.db.engine.connect() as conn:
            return list(conn.execute(select(WorkerCommandRow).where(WorkerCommandRow.done_at.is_(None)).order_by(WorkerCommandRow.id)))

    def _finish(self, command_id: int, result: dict[str, Any]) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(update(WorkerCommandRow).where(WorkerCommandRow.id == command_id).values(done_at=utcnow(), result=result))

    def _prune(self) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(delete(WorkerCommandRow).where(WorkerCommandRow.done_at < utcnow() - KEEP_COMMANDS))

    def _job(self, job: Any) -> dict[str, Any]:
        nxt = getattr(job, "next_run_time", None)  # a job added before the scheduler starts has none yet
        return {"id": job.id, "name": job.name, "next_run_at": _iso(nxt), "paused": self.scheduler.running and nxt is None}

    def _publish(self, status: str | None = None) -> None:
        state = self.scheduler.state
        r = self.service.retention_state
        row = {
            "id": STATE_ID,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "heartbeat_at": utcnow(),
            "scheduler": status or {STATE_RUNNING: "running", STATE_PAUSED: "paused"}.get(state, "starting"),
            "jobs": [self._job(j) for j in self.scheduler.get_jobs()],
            "retention": {
                "last_run_at": _iso(r.last_run_at),
                "last_success_at": _iso(r.last_success_at),
                "last_removed": r.last_removed,
                "last_error": r.last_error,
                "running": self.service.retention_running,
            },
            "changes": Database.changes,
        }
        with self.db.engine.begin() as conn:
            stmt = insert(WorkerStateRow).values(row)  # on the engine: the heartbeat is no data change
            conn.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={k: stmt.excluded[k] for k in row if k != "id"}))


def _report(name: str) -> Callable[[Future[RunResult]], None]:
    def done(future: Future[RunResult]) -> None:
        if exc := future.exception():
            log.error("worker: admin run of %s failed: %s", name, exc)

    return done


def main(settings: Settings) -> int:
    db = Database(settings.database.url, settings.database.echo, settings.database)
    db.create_schema()
    asyncio.run(Worker(settings, db).run())
    return 0
