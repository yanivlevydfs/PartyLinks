"""Runs sources end-to-end: scrape -> enrich -> persist -> artwork -> record the run.

Isolation model: every source run executes on its own worker thread with its own
asyncio event loop, HTTP client, lock and timeout. Inside that loop the source's
requests run concurrently (listing pages, event details, artwork). A slow, hung,
failing or blocked site therefore never delays or breaks any other source.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from partylinks.config import Settings
from partylinks.db import Database, ScrapeRunRow, start_run, upsert_events, utcnow
from partylinks.enrichment import Enricher
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent
from partylinks.retention import purge_expired, retention_cutoff
from partylinks.scrapers import SCRAPERS, build_scraper
from partylinks.scrapers.base import SourceBlockedError

log = logging.getLogger(__name__)

SAVE_BATCH_SIZE = 200


class SourceBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunResult:
    run_id: int
    source: str
    status: str
    events_found: int
    events_saved: int
    events_failed: int
    error: str | None


class ScrapeService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.enricher = Enricher(settings.enrichment, settings.timezone)
        self.media = MediaStore(settings.media, db)
        self._locks = {name: threading.Lock() for name in SCRAPERS}
        self._retention_lock = threading.Lock()
        # One thread per source (+1 for retention) so everything can run at the same time.
        self._executor = ThreadPoolExecutor(max_workers=len(SCRAPERS) + 1, thread_name_prefix="partylinks")

    def is_running(self, source: str) -> bool:
        return self._locks[source].locked()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def run_source(self, source: str, trigger: str = "manual") -> RunResult:
        """Run one source on its own thread; raises SourceBusyError if it is already running."""
        lock = self._locks[source]
        if not lock.acquire(blocking=False):
            raise SourceBusyError(f"{source} is already running")
        try:
            future = self._executor.submit(self._run_in_thread, source, trigger, lock)
        except BaseException:
            lock.release()
            raise
        return await asyncio.wrap_future(future)

    async def run_sources(self, sources: list[str], trigger: str) -> list[RunResult | BaseException]:
        """Run several sources in parallel; one failing never affects the others."""
        return await asyncio.gather(*(self.run_source(s, trigger) for s in sources), return_exceptions=True)

    async def purge_expired(self) -> int:
        return await asyncio.wrap_future(self._executor.submit(self._purge_in_thread))

    # ------------------------------------------------------------------ worker thread
    def _run_in_thread(self, source: str, trigger: str, lock: threading.Lock) -> RunResult:
        try:
            return asyncio.run(self._run(source, trigger))
        finally:
            lock.release()  # released by the worker, so a cancelled caller can't cause overlap

    def _purge_in_thread(self) -> int:
        with self._retention_lock:
            return purge_expired(self.db, self.media, self.settings.retention, self.settings.timezone)

    async def _run(self, source: str, trigger: str) -> RunResult:
        run_id = await asyncio.to_thread(self._create_run, source, trigger)
        found = saved = 0
        status, error = "success", None
        scraper = None
        timeout = self.settings.scheduler.run_timeout_seconds
        cutoff = retention_cutoff(self.settings.retention, self.settings.timezone)
        try:
            async with asyncio.timeout(timeout):
                scraper = build_scraper(source, self.settings)
                batch: dict[str, ScrapedEvent] = {}
                blocked: SourceBlockedError | None = None
                async with scraper:
                    try:
                        async for event in scraper.scrape():
                            found += 1
                            if _finished_before(event, cutoff):  # already past retention; don't store
                                continue
                            if (enriched := self.enricher.enrich(event)) is not None:
                                batch[enriched.event_id] = enriched
                            if len(batch) >= SAVE_BATCH_SIZE:
                                saved += await asyncio.to_thread(self._save, list(batch.values()))
                                batch.clear()
                    except SourceBlockedError as exc:
                        blocked = exc  # keep whatever was collected before the site refused access
                if batch:
                    saved += await asyncio.to_thread(self._save, list(batch.values()))
                if blocked:
                    status, error = "blocked", str(blocked)
                    log.warning("source %s blocked: %s (saved %d events)", source, blocked, saved)
                elif scraper.failures:
                    status = "partial"
                if self.settings.media.download:
                    await self.media.download_pending(source, scraper.http)
        except TimeoutError:
            status, error = "failed", f"timed out after {timeout}s (saved {saved} events)"
            log.error("source %s %s", source, error)
        except Exception as exc:  # noqa: BLE001 - a run must always be recorded
            log.exception("source %s failed", source)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        failures = scraper.failures if scraper else []
        result = RunResult(run_id, source, status, found, saved, len(failures), error)
        await asyncio.to_thread(self._finish_run, result, failures)
        log.info("source %s finished: %s", source, result)
        return result

    # ------------------------------------------------------------------ blocking DB helpers
    def _create_run(self, source: str, trigger: str) -> int:
        with self.db.session() as session:
            return start_run(session, source, trigger).id

    def _save(self, events: list[ScrapedEvent]) -> int:
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
    finished = event.end_at or event.start_at
    return finished is not None and finished < cutoff
