"""Runs sources end-to-end: scrape -> enrich -> persist -> artwork -> regroup -> record.

Isolation model: every source run executes on its own worker thread with its own
asyncio event loop, HTTP client, lock and timeout. Inside that loop the source's
requests run concurrently (listing pages, event details, artwork). A slow, hung,
failing or blocked site therefore never delays or breaks any other source.

Resilience:
* one event that fails enrichment is recorded and skipped, never the batch;
* collected events are always flushed (also on timeout, block or error);
* DB writes retry on lock contention, and a batch the DB rejects falls back to
  row-by-row so one bad row cannot poison the others;
* artwork download and duplicate regrouping run after the scrape, outside its
  timeout; their failures only downgrade the run to ``partial``;
* a run is always recorded (finish is retried); runs left ``running`` by a killed
  process are marked ``interrupted`` at startup;
* ``shutdown()`` asks running sources to stop at the next event.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from partylinks.config import Settings
from partylinks.db import Database, EventRow, ScrapeRunRow, start_run, upsert_events, utcnow
from partylinks.dedup import Deduplicator
from partylinks.enrichment import Enricher
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent
from partylinks.retention import purge_expired, retention_cutoff
from partylinks.scrapers import SCRAPERS, build_scraper
from partylinks.scrapers.base import SourceBlockedError

log = logging.getLogger(__name__)

SAVE_BATCH_SIZE = 200
DB_RETRIES = 4
T = TypeVar("T")


class SourceBusyError(RuntimeError):
    pass


class _StopRequested(Exception):
    """Raised inside a run when the service is shutting down."""


@dataclass(frozen=True)
class RunResult:
    run_id: int
    source: str
    status: str
    events_found: int
    events_saved: int
    events_failed: int
    error: str | None


@dataclass
class RetentionState:
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_removed: int | None = None
    last_error: str | None = None
    history: list[str] = field(default_factory=list)


def _with_db_retry(what: str, fn: Callable[[], T]) -> T:
    """Retry a blocking DB call on lock contention (SQLite 'database is locked')."""
    for attempt in range(1, DB_RETRIES + 1):
        try:
            return fn()
        except OperationalError as exc:
            if attempt == DB_RETRIES or "locked" not in str(exc).lower() and "deadlock" not in str(exc).lower():
                raise
            log.warning("%s: database busy (attempt %d/%d), retrying", what, attempt, DB_RETRIES)
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


class ScrapeService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.enricher = Enricher(settings.enrichment, settings.timezone)
        self.media = MediaStore(settings.media, db)
        self.deduplicator = Deduplicator(settings.dedup)
        self._locks = {name: threading.Lock() for name in SCRAPERS}
        self._retention_lock = threading.Lock()
        self._dedup_lock = threading.Lock()  # regrouping spans all sources
        self._stopping = threading.Event()
        # One thread per source so all sources can run at the same time; retention has its own.
        self._executor = ThreadPoolExecutor(max_workers=len(SCRAPERS), thread_name_prefix="partylinks-src")
        self._maintenance = ThreadPoolExecutor(max_workers=1, thread_name_prefix="partylinks-maint")
        self.retention_state = RetentionState()
        db.mark_interrupted_runs()

    def is_running(self, source: str) -> bool:
        return self._locks[source].locked()

    @property
    def retention_running(self) -> bool:
        return self._retention_lock.locked()

    def shutdown(self) -> None:
        """Ask running sources to stop at the next event and stop accepting work."""
        self._stopping.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._maintenance.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ starting runs
    def start_source(self, source: str, trigger: str = "manual") -> Future[RunResult]:
        """Claim the source and submit its run. Raises SourceBusyError synchronously."""
        if self._stopping.is_set():
            raise SourceBusyError("service is shutting down")
        lock = self._locks[source]
        if not lock.acquire(blocking=False):
            raise SourceBusyError(f"{source} is already running")
        try:
            future = self._executor.submit(self._run_in_thread, source, trigger, lock)
        except BaseException:
            lock.release()
            raise
        # Cancelled while still queued (shutdown): the worker never runs, so release here.
        future.add_done_callback(lambda f: lock.release() if f.cancelled() else None)
        return future

    async def run_source(self, source: str, trigger: str = "manual") -> RunResult:
        """Run one source on its own thread; raises SourceBusyError if it is already running."""
        return await asyncio.wrap_future(self.start_source(source, trigger))

    async def run_sources(self, sources: list[str], trigger: str) -> list[RunResult | BaseException]:
        """Run several sources in parallel; one failing never affects the others."""
        return await asyncio.gather(*(self.run_source(s, trigger) for s in sources), return_exceptions=True)

    def recompute_areas(self) -> int:
        """Re-derive ``area`` for every stored event from the current config. Returns rows changed."""
        matcher = self.enricher.areas
        changed = 0
        with self.db.session() as session:
            for row in session.scalars(select(EventRow)):
                area = matcher.match(row.venue_name, row.address, row.latitude, row.longitude, row.in_target_city)
                if area != row.area:
                    row.area, changed = area, changed + 1
        log.info("areas: updated %d events", changed)
        return changed

    def regroup(self) -> int:
        """Recompute duplicate groups across all sources (serialized between source threads)."""
        with self._dedup_lock:
            return _with_db_retry("regroup", self._regroup_once)

    def _regroup_once(self) -> int:
        with self.db.session() as session:
            return self.deduplicator.regroup(session)

    async def purge_expired(self) -> int:
        """Retention on the dedicated maintenance thread; records its outcome for /health."""
        if self.retention_running:
            raise SourceBusyError("retention is already running")
        return await asyncio.wrap_future(self._maintenance.submit(self._purge_in_thread))

    # ------------------------------------------------------------------ worker threads
    def _run_in_thread(self, source: str, trigger: str, lock: threading.Lock) -> RunResult:
        try:
            return asyncio.run(self._run(source, trigger))
        finally:
            lock.release()

    def _purge_in_thread(self) -> int:
        state = self.retention_state
        with self._retention_lock:
            state.last_run_at = utcnow()
            try:
                removed = _with_db_retry(
                    "retention",
                    lambda: purge_expired(self.db, self.media, self.settings.retention, self.settings.timezone),
                )
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.exception("retention failed")
                raise
            state.last_success_at, state.last_removed, state.last_error = utcnow(), removed, None
            return removed

    async def _run(self, source: str, trigger: str) -> RunResult:
        run_id = await asyncio.to_thread(_with_db_retry, "start run", lambda: self._create_run(source, trigger))
        found = saved = 0
        status, error = "success", None
        scraper = None
        failures: list[str] = []
        batch: dict[str, ScrapedEvent] = {}
        timeout = self.settings.scheduler.run_timeout_seconds
        cutoff = retention_cutoff(self.settings.retention, self.settings.timezone)

        async def flush() -> None:
            nonlocal saved
            if batch:
                events = list(batch.values())
                batch.clear()
                saved += await asyncio.to_thread(self._save, events, failures)

        deadline = asyncio.timeout(timeout)
        try:
            async with deadline:
                scraper = build_scraper(source, self.settings)
                failures = scraper.failures
                async with scraper:
                    try:
                        async for event in scraper.scrape():
                            if self._stopping.is_set():
                                raise _StopRequested
                            found += 1
                            if _finished_before(event, cutoff):  # already past retention; don't store
                                continue
                            try:
                                enriched = self.enricher.enrich(event)
                            except Exception as exc:  # noqa: BLE001 - one odd event never costs the batch
                                scraper.record_failure(f"{event.event_id} enrichment", exc)
                                continue
                            if enriched is not None:
                                batch[enriched.event_id] = enriched
                            if len(batch) >= SAVE_BATCH_SIZE:
                                await flush()
                    except SourceBlockedError as exc:
                        status, error = "blocked", str(exc)  # keep what was collected before the refusal
                        log.warning("source %s blocked: %s", source, exc)
        except TimeoutError:
            if deadline.expired():
                status, error = "failed", f"timed out after {timeout}s"
            else:  # a TimeoutError raised by the scraper itself
                status, error = "failed", "TimeoutError inside the scraper"
            log.error("source %s %s", source, error)
        except _StopRequested:
            status, error = "interrupted", "service shutting down"
        except Exception as exc:  # noqa: BLE001 - a run must always be recorded
            log.exception("source %s failed", source)
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        finally:
            try:
                await flush()  # whatever was collected is always saved
            except Exception as exc:  # noqa: BLE001
                log.exception("source %s: final save failed", source)
                status, error = "failed", f"save failed: {type(exc).__name__}: {exc}"[:500]

        # Post-processing: outside the scrape timeout; failures only downgrade to partial.
        if saved > 0 and scraper is not None and not self._stopping.is_set():
            if self.settings.media.download:
                await self._post_step(source, "artwork", failures, self.media.download_pending(source, scraper.http))
            if self.settings.dedup.enabled:
                await self._post_step(source, "regroup", failures, asyncio.to_thread(self.regroup))
        if status == "success" and failures:
            status = "partial"

        result = RunResult(run_id, source, status, found, saved, len(failures), error)
        try:
            await asyncio.to_thread(_with_db_retry, "finish run", lambda: self._finish_run(result, failures))
        except Exception:  # noqa: BLE001 - startup recovery marks it interrupted later
            log.exception("source %s: could not record run %s", source, run_id)
        log.info("source %s finished: %s", source, result)
        return result

    @staticmethod
    async def _post_step(source: str, what: str, failures: list[str], step: Any) -> None:
        try:
            await step
        except Exception as exc:  # noqa: BLE001 - events are already saved
            log.exception("source %s: %s failed", source, what)
            failures.append(f"{what}: {type(exc).__name__}: {exc}"[:500])

    # ------------------------------------------------------------------ blocking DB helpers
    def _create_run(self, source: str, trigger: str) -> int:
        with self.db.session() as session:
            return start_run(session, source, trigger).id

    def _save(self, events: list[ScrapedEvent], failures: list[str]) -> int:
        """Save a batch; if the DB rejects it (bad value in one row), save row by row."""
        try:
            return _with_db_retry("save", lambda: self._save_batch(events))
        except (DataError, IntegrityError) as exc:
            log.warning("batch of %d rejected (%s); saving row by row", len(events), type(exc).__name__)
        saved = 0
        for event in events:
            try:
                saved += _with_db_retry("save", lambda event=event: self._save_batch([event]))
            except (DataError, IntegrityError) as exc:
                failures.append(f"{event.event_id} not saved: {type(exc).__name__}: {str(exc).splitlines()[0]}"[:500])
        return saved

    def _save_batch(self, events: list[ScrapedEvent]) -> int:
        with self.db.session() as session:
            saved, orphaned_files = upsert_events(session, events)
        self.media.delete_files(orphaned_files)
        return saved

    def _finish_run(self, result: RunResult, failures: list[str]) -> None:
        with self.db.session() as session:
            run = session.get(ScrapeRunRow, result.run_id)
            if run is None:
                raise LookupError(f"scrape run {result.run_id} disappeared before it could be finalized")
            run.status = result.status
            run.finished_at = utcnow()
            run.events_found = result.events_found
            run.events_saved = result.events_saved
            run.events_failed = result.events_failed
            run.error = result.error
            run.failures = failures[:200]


def _finished_before(event: ScrapedEvent, cutoff: datetime) -> bool:
    """Same rule as retention: started before the cutoff -> never stored."""
    return event.start_at is not None and event.start_at < cutoff
