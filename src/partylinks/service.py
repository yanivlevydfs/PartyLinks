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
import hashlib
import json
import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from partylinks import group_fill
from partylinks.artistdb import ArtistBook
from partylinks.config import Settings
from partylinks.db import (
    Database,
    EventRow,
    FlagRow,
    ImageRow,
    ScrapeRunRow,
    bulk_update_by_id,
    flag_on,
    record_delisted,
    start_run,
    upcoming_filter,
    upsert_events,
    utcnow,
)
from partylinks.db import with_db_retry as _with_db_retry
from partylinks.dedup import Deduplicator
from partylinks.enrichment import ABROAD_AREA, UNKNOWN_COUNTRY, Enricher, meters
from partylinks.geo import Geocoder
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent
from partylinks.parsing import is_site_logo
from partylinks.places import all_cities
from partylinks.popularity import models as popularity
from partylinks.retention import is_current, purge_expired
from partylinks.runs.health import RunHealth
from partylinks.scrapers import SCRAPERS, build_scraper, source_settings
from partylinks.scrapers.base import SourceBlockedError
from partylinks.venue_profiles import VenueBook
from partylinks.venues import venue_updates

log = logging.getLogger(__name__)

SAVE_BATCH_SIZE = 200


class Coalesced:
    """A whole-table pass that many finishing sources ask for at once (regroup, geocode). One runs;
    the requests that arrive meanwhile share a single re-run instead of each running a full pass
    (74 sources x 3 passes once starved the writers, Oct 2026)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self._running = threading.Lock()
        self._state = threading.Lock()
        self._queued = False

    def __call__(self) -> None:
        with self._state:
            if self._queued:
                return  # a re-run is already waiting; it starts after this request, so it covers it
            self._queued = True
        with self._running:
            with self._state:
                self._queued = False
            self._fn()


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


class ScrapeService:
    def __init__(self, settings: Settings, db: Database, owns_runs: bool = True) -> None:
        """``owns_runs``: this process runs the sources (false in the web process when a worker does)."""
        self.settings = settings
        self.db = db
        self.enricher = Enricher(settings.enrichment, settings.timezone)
        self.artists = ArtistBook(db)
        self.venues = VenueBook(db, settings.media.webp_quality)
        if not self.artists.load()[0]:  # first start: seed the dictionary from the lineups already stored
            with db.session() as session:
                lineups = list(session.scalars(select(EventRow.lineup)))
                self.artists.record((n for lineup in lineups for n in lineup or []), session)
        self.media = MediaStore(settings.media, db)
        self.deduplicator = Deduplicator(settings.dedup)
        self.geocoder = Geocoder(
            settings.geocoding, self.enricher.areas, settings.enrichment.target_city.aliases, db,
            all_cities(settings.enrichment.cities, settings.enrichment.places_min_population),
        )
        self._geo_lock = threading.Lock()  # one geocoding pass at a time (Nominatim rate limit)
        self._locks = {name: threading.Lock() for name in SCRAPERS}
        self._retention_lock = threading.Lock()
        self._dedup_lock = threading.Lock()  # regrouping spans all sources
        # After a run: shared by all sources finishing together (see Coalesced).
        self._regroup_soon, self._geocode_soon = Coalesced(self.regroup), Coalesced(self.geocode)
        self._stopping = threading.Event()
        # At most max_parallel_runs sources at once (proxy pool, memory); the rest queue. Retention has its own.
        self._executor = ThreadPoolExecutor(
            max_workers=settings.scheduler.max_parallel_runs, thread_name_prefix="partylinks-src"
        )
        self._queued: set[str] = set()  # claimed, waiting for a free slot
        self.health = RunHealth(settings, db, self.start_source, self._stopping)
        self._maintenance = ThreadPoolExecutor(max_workers=1, thread_name_prefix="partylinks-maint")
        self.retention_state = RetentionState()
        if owns_runs:  # runs left "running" by a process that died; never the live worker's
            db.mark_interrupted_runs()

    def is_running(self, source: str) -> bool:
        """Running or queued (the source is claimed either way)."""
        return self._locks[source].locked()

    def is_queued(self, source: str) -> bool:
        return source in self._queued

    @property
    def retention_running(self) -> bool:
        return self._retention_lock.locked()

    def shutdown(self) -> None:
        """Ask running sources to stop at the next event and stop accepting work."""
        self._stopping.set()
        self.media.stop()
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
        self._queued.add(source)
        try:
            future = self._executor.submit(self._run_in_thread, source, trigger, lock)
        except BaseException:
            self._queued.discard(source)
            lock.release()
            raise
        # Cancelled while still queued (shutdown): the worker never runs, so release here.
        future.add_done_callback(lambda f: (self._queued.discard(source), lock.release()) if f.cancelled() else None)
        return future

    async def run_source(self, source: str, trigger: str = "manual") -> RunResult:
        """Run one source on its own thread; raises SourceBusyError if it is already running."""
        return await asyncio.wrap_future(self.start_source(source, trigger))

    async def run_sources(self, sources: list[str], trigger: str) -> list[RunResult | BaseException]:
        """Run several sources in parallel; one failing never affects the others."""
        return await asyncio.gather(*(self.run_source(s, trigger) for s in sources), return_exceptions=True)

    def geocode(self, network: bool = True) -> dict[str, int]:
        """Give map coordinates to upcoming events that have only an address."""
        with self._geo_lock:
            stats = self.geocoder.locate_missing(network, stop=self._stopping).as_dict()
        self.recompute_areas()  # new events and new coordinates: place them in a city / region
        return stats

    def recompute_genres(self, force: bool = False) -> int:
        """Re-read the genres of every stored event when the genre config changed, so a genre added
        to settings.yaml (or a keyword changed) reaches events scraped before it. The config's
        fingerprint is kept in the flags table; an unchanged config costs one lookup. Returns rows changed.

        ponytail: ~3 ms an event (~7 min at 144k rows, Nov 2026), read outside the write lock; a scrape that saves an
        event meanwhile may be overwritten with its older text's genres until its next run.
        """
        fields = {"genres", "declared_genres", "genre_families", "detect_genres_from_text", "text_ignore",
                  "talk_keywords", "talk_genres", "declared_text_genres", "declared_genres_beside", "source_default_genres", "title_genres", "fallback_genres"}  # fmt: skip
        config = json.dumps(self.settings.enrichment.model_dump(include=fields), sort_keys=True, ensure_ascii=False)
        key = "genres:" + hashlib.sha256(config.encode()).hexdigest()[:16]
        with self.db.session(write=False) as session:
            if not force and flag_on(session, key):
                return 0
            rows = session.execute(
                select(EventRow.id, EventRow.source, EventRow.title, EventRow.description, EventRow.source_genres,
                       EventRow.event_types, EventRow.genres)
            ).all()  # fmt: skip
        updates = []
        for r in rows:
            probe = ScrapedEvent.model_construct(
                source=r.source, title=r.title, description=r.description,
                source_genres=r.source_genres or [], event_types=r.event_types or [],
            )  # fmt: skip
            genres = self.enricher._detect_genres(probe)
            if genres != (r.genres or []):
                updates.append({"id": r.id, "genres": genres})
        with self.db.session() as session:
            if updates:
                session.execute(update(EventRow), updates)
            session.execute(delete(FlagRow).where(FlagRow.key.like("genres:%")))
            session.add(FlagRow(key=key, on=True))
        log.info("genres: config changed, updated %d of %d events", len(updates), len(rows))
        return len(updates)

    def recompute_areas(self) -> int:
        """Re-derive the canonical ``city``, ``in_target_city`` and ``area`` of every stored event from
        the current config. An event with only a venue name takes the city other events at that venue
        name, else its coordinates decide. Returns rows changed."""
        enricher, matcher = self.enricher, self.enricher.areas
        target = next(
            (c for c in self.settings.enrichment.cities if c.en == self.settings.enrichment.target_city.name), None
        )
        # Read and compute outside the write lock (a whole-table pass); write only the changed rows.
        with self.db.session(write=False) as session:
            rows = session.execute(select(
                EventRow.id, EventRow.source, EventRow.venue_name, EventRow.address, EventRow.city,
                EventRow.latitude, EventRow.longitude, EventRow.area, EventRow.in_target_city, EventRow.country,
            )).all()  # fmt: skip
        cities = [enricher.canonical_city(r.city, r.address, r.venue_name) for r in rows]
        inside = [enricher.in_target_city(c, r.address, r.venue_name) for r, c in zip(rows, cities, strict=True)]
        # Venues whose events give a city or address: the most common answer per venue name.
        votes = Counter(
            (r.venue_name.casefold(), i) for r, i in zip(rows, inside, strict=True)
            if r.venue_name and i is not None and (r.city or r.address)
        )
        venue_inside: dict[str, bool] = {}
        for (venue, i), _ in votes.most_common():
            venue_inside.setdefault(venue, i)
        updates = []
        for row, city, here in zip(rows, cities, inside, strict=True):
            if here is None and row.venue_name:
                here = venue_inside.get(row.venue_name.casefold())
            if here is None and row.latitude is not None and row.longitude is not None and target and target.center:
                here = meters(target.center, (row.latitude, row.longitude)) <= target.radius_m
            area = matcher.match(row.venue_name, row.address, row.latitude, row.longitude, here, city, row.country)
            scraper = SCRAPERS.get(row.source)
            abroad = area == ABROAD_AREA or (scraper is not None and scraper.abroad_only)
            country = row.country or (UNKNOWN_COUNTRY if abroad else None)
            if (area, city, here, country) != (row.area, row.city, row.in_target_city, row.country):
                updates.append({"id": row.id, "area": area, "city": city, "in_target_city": here, "country": country})
        if updates:
            # ponytail: a row saved between the read and this write keeps its fresh values until the next pass.
            with self.db.session() as session:
                session.execute(update(EventRow), updates)
        changed = len(updates)
        log.debug("areas/cities: updated %d events", changed)
        return changed

    def regroup(self) -> int:
        """Recompute duplicate groups across all sources (serialized between source threads)."""
        with self._dedup_lock:
            return _with_db_retry("regroup", self._regroup_once)

    @contextmanager
    def _planning(self) -> Iterator[Session]:
        """A read session for the regroup's whole-table planning: it computes between its reads, longer than
        the idle-in-transaction cut-off allows while many sources run (database.regroup_idle_timeout_ms)."""
        with self.db.session(write=False) as session:
            ms = self.settings.database.regroup_idle_timeout_ms
            session.execute(text(f"SET LOCAL idle_in_transaction_session_timeout = {int(ms)}"))
            yield session

    def _regroup_once(self) -> int:
        # Whole-table passes over ~40k rows: computed in read sessions, the write lock only for the
        # changed rows (it was held for minutes and starved every save, Oct 2026). ponytail: a row
        # saved between a read and its write is regrouped on the next pass.
        with self._planning() as session:
            renames = venue_updates(session)  # before grouping: a shared venue helps match duplicates
        if renames:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, renames)
        log.debug("venues: renamed %d events", len(renames))
        with self._planning() as session:
            merged, changed = self.deduplicator.plan(session)
        if changed:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, changed)
        with self._planning() as session:
            fills = group_fill.plan(session, self.enricher.clean_name, frozenset(self.settings.dedup.place_sources))  # what one store lacks, from the event's other stores / its venue
        if fills:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, fills)
        log.debug("group fill: %d events", len(fills))
        return merged

    async def purge_expired(self) -> int:
        """Retention on the dedicated maintenance thread; records its outcome for /health."""
        if self.retention_running:
            raise SourceBusyError("retention is already running")
        return await asyncio.wrap_future(self._maintenance.submit(self._purge_in_thread))

    # ------------------------------------------------------------------ worker threads
    async def _blocking[R](self, fn: Callable[..., R], *args: Any) -> R:
        """Run blocking work off the event loop; inline while shutting down. The interpreter then
        refuses new thread-pool work ("cannot schedule new futures after shutdown"), and this source
        has its own thread anyway, so saving inline only holds up this source."""
        if self._stopping.is_set():
            return fn(*args)
        return await asyncio.to_thread(fn, *args)

    def _run_in_thread(self, source: str, trigger: str, lock: threading.Lock) -> RunResult:
        self._queued.discard(source)
        try:
            result = asyncio.run(self._run(source, trigger))
        finally:
            lock.release()
        try:
            self.health.after_run(result, trigger)  # retry a bad run later; pause a broken source
        except Exception:  # the run is recorded; a DB blip here must not turn it into an error
            log.exception("source %s: health check after the run failed", source)
        return result

    def _purge_in_thread(self) -> int:
        state = self.retention_state
        with self._retention_lock:
            state.last_run_at = utcnow()
            try:
                removed = _with_db_retry(
                    "retention",
                    lambda: purge_expired(
                        self.db, self.media, self.settings.retention, self.settings.timezone,
                        drop_other_cities=self.settings.enrichment.target_city.drop_other_cities,
                    ),
                )
                popularity.prune(self.db, self.settings.popularity, self.settings.timezone)  # old view rows
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
        timeout = self.settings.scheduler.run_timeout_seconds  # the same for every source

        async def flush() -> None:
            nonlocal saved
            if batch:
                events = list(batch.values())
                batch.clear()
                saved += await self._blocking(self._save, events, failures)
                # Each batch's images now, not only after the run: deploys every ~30 min cut long runs
                # (munievents, 2026-11-11), so their pictures were never stored and the site logo stayed.
                if scraper is not None and self.settings.media.download and not self._stopping.is_set():
                    step = self.media.download_pending(source, scraper.working_http, progress=alive)
                    await self._post_step(source, "artwork", failures, step)

        # The stall watchdog is the only stop: a run goes on for as long as it makes progress. Each sign of
        # life (a new event, an answer from the site, an image chunk stored, a post-processing step) pushes
        # the watchdog back by stall_timeout_seconds. run_timeout_seconds > 0 adds an optional cap.
        stall = self.settings.scheduler.stall_timeout_seconds
        loop = asyncio.get_running_loop()
        hard_end = loop.time() + timeout if timeout else math.inf

        def until() -> float:
            return min(hard_end, loop.time() + stall)

        deadline = asyncio.timeout_at(until())
        watchdog = [deadline]  # the scrape's, then post-processing's

        def alive() -> None:
            with suppress(RuntimeError):  # the final save, between the two watchdogs: none to push back
                watchdog[0].reschedule(until())

        try:
            async with deadline:
                # Inside the try: a failure here is recorded as this run's, not left "running" until a restart.
                known, blocked = await asyncio.to_thread(self.artists.load)
                profiles = await asyncio.to_thread(self.artists.profiles)
                self.enricher.learn_artists(known, blocked, featured=[p.name for p in profiles.values()])
                scraper = build_scraper(source, self.settings)
                scraper.known_images = await asyncio.to_thread(self._known_images, source)
                scraper.linked_urls = await asyncio.to_thread(self._linked_urls, source)
                artwork = await asyncio.to_thread(self._artwork_by_title, source)
                scraper.known_artists = {key: p for key, p in profiles.items() if p.source == source}
                scraper.wanted = lambda event: not self.enricher.is_dropped(event)
                # Shotgun, slowed by its pace, read sitemaps whose events were all filtered out for over 15
                # minutes and was stopped as stuck (2026-11-10): any answer from the site is progress too.
                scraper.progress = alive
                failures = scraper.failures
                async with scraper:
                    try:
                        async for event in scraper.scrape():
                            if self._stopping.is_set():
                                raise _StopRequested
                            found += 1
                            alive()  # progress: not stuck
                            scraper.artist_profiles += event.artist_profiles
                            if _finished_before(event, self.settings):  # already over; never stored
                                continue
                            try:
                                enriched = self.enricher.enrich(event)
                            except Exception as exc:  # noqa: BLE001 - one odd event never costs the batch
                                scraper.record_failure(f"{event.event_id} enrichment", exc)
                                continue
                            if enriched is not None:
                                if not enriched.images:
                                    fill_artwork(enriched, scraper.known_images, artwork)
                                batch[enriched.event_id] = enriched
                            if len(batch) >= SAVE_BATCH_SIZE:
                                await flush()
                                alive()  # its images were progress
                    except SourceBlockedError as exc:
                        status, error = "blocked", str(exc)  # keep what was collected before the refusal
                        log.warning("source %s blocked: %s", source, exc)
        except TimeoutError:
            if deadline.expired():
                # asyncio fires a timer up to its clock resolution early (~15 ms on Windows): at the run
                # limit, "now" can read a hair before it.
                stalled = loop.time() < hard_end - 0.5
                status, error = "failed", f"stuck: no progress for {stall}s" if stalled else f"timed out after {timeout}s"
            else:  # a TimeoutError raised by the scraper itself
                status, error = "failed", "TimeoutError inside the scraper"
            log.error("source %s %s", source, error)
        except _StopRequested:
            status, error = "interrupted", "service shutting down"
        except Exception as exc:
            if self._stopping.is_set():  # a deploy closed the thread pools mid-run: not the source's fault
                status, error = "interrupted", "service shutting down"
            else:
                log.exception("source %s failed", source)
                status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        finally:
            try:
                await flush()  # whatever was collected is always saved
            except Exception as exc:
                log.exception("source %s: final save failed", source)
                status, error = "failed", f"save failed: {type(exc).__name__}: {exc}"[:500]

        # Post-processing: under the same stall watchdog (and cap, with at least a minute to finish up);
        # failures only downgrade to partial.
        post_deadline = asyncio.timeout_at(max(until(), loop.time() + 60))
        watchdog[0] = post_deadline
        try:
            async with post_deadline:
                await self._post_process(source, scraper, saved, failures, alive)
        except TimeoutError:
            if not post_deadline.expired():
                raise
            stuck = loop.time() < hard_end - 0.5
            failures.append(f"post-processing stuck: no progress for {stall}s" if stuck else f"post-processing stopped at the {timeout}s run limit")
        if status == "success" and failures:
            status = "partial"

        if not self._stopping.is_set():  # shutting down: the run is recorded as interrupted on next start
            try:
                await asyncio.to_thread(_with_db_retry, "track changes", lambda: self._track_changes(source, run_id, status, found))
            except Exception:
                log.exception("source %s: change tracking failed", source)

        result = RunResult(run_id, source, status, found, saved, len(failures), error)
        try:
            await self._blocking(_with_db_retry, "finish run", lambda: self._finish_run(result, failures))
        except Exception:
            log.exception("source %s: could not record run %s", source, run_id)
        log.info("source %s finished: %s", source, result)
        return result

    async def _post_process(
        self, source: str, scraper: Any, saved: int, failures: list[str], alive: Callable[[], None] = lambda: None
    ) -> None:
        """After the scrape: the new events' images, regroup, geocode, and the artist and venue profiles.
        ``alive`` pushes the run's stall watchdog back: at each step and each image chunk stored."""
        if saved > 0 and scraper is not None and not self._stopping.is_set():
            if self.settings.media.download:
                alive()
                step = self.media.download_pending(source, scraper.working_http, progress=alive)
                await self._post_step(source, "artwork", failures, step)
            if self.settings.dedup.enabled:
                alive()
                await self._post_step(source, "regroup", failures, self._alive_while(asyncio.to_thread(self._regroup_soon), alive))
            # Saving overwrote coordinates with what the source sent; restore / look up the rest.
            alive()
            await self._post_step(source, "geocode", failures, self._alive_while(asyncio.to_thread(self._geocode_soon), alive))
        if scraper is not None and scraper.artist_profiles and not self._stopping.is_set():  # lineups, next run on
            alive()
            await self._post_step(
                source,
                "artist profiles",
                failures,
                self._alive_while(asyncio.to_thread(self.artists.save_profiles, scraper.artist_profiles), alive),
            )
        if scraper is not None and scraper.venue_profiles and not self._stopping.is_set():
            alive()
            step = self._alive_while(asyncio.to_thread(self.venues.save, scraper.venue_profiles), alive)
            await self._post_step(source, "venue profiles", failures, step)

    async def _alive_while(self, step: Any, alive: Callable[[], None]) -> Any:
        """A blocking step in a thread cannot report progress: it is alive while its thread works, so the
        run's stall watchdog is pushed back every few seconds until it ends. A query that hangs is stopped
        by the database itself (statement_timeout, lock_timeout, idle_in_transaction_session_timeout)."""
        every = min(5.0, self.settings.scheduler.stall_timeout_seconds / 4)
        task = asyncio.ensure_future(step)
        try:
            while not (await asyncio.wait({task}, timeout=every))[0]:
                alive()
            return task.result()
        finally:
            task.cancel()  # the watchdog fired (a stuck step): stop waiting for it

    @staticmethod
    async def _post_step(source: str, what: str, failures: list[str], step: Any) -> None:
        try:
            await step
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "after shutdown" in str(exc):  # a deploy closed the thread pool
                log.info("source %s: %s skipped, service shutting down", source, what)
                return
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

    def _known_images(self, source: str) -> dict[str, list[str]]:
        """Stored artwork URLs per event of ``source``, in display order."""
        known: dict[str, list[str]] = {}
        with self.db.session(write=False) as session:
            rows = session.execute(
                select(EventRow.source_event_id, ImageRow.url)
                .join(ImageRow, ImageRow.event_id == EventRow.id)
                .where(EventRow.source == source)
                .order_by(EventRow.id, ImageRow.position)
            )
            for event_id, url in rows:
                known.setdefault(event_id, []).append(url)
        return known

    def _linked_urls(self, source: str) -> list[str]:
        """Ticket links of other sources' upcoming events (aggregators such as Popit link out)."""
        url = EventRow.extra["purchase_url"].as_string()
        with self.db.session(write=False) as session:
            rows = session.scalars(select(url).where(EventRow.source != source, url.is_not(None), upcoming_filter()))
            return list(dict.fromkeys(rows))

    def _artwork_by_title(self, source: str) -> dict[str, str]:
        """Stored primary artwork per title key; ``source``'s own listings win over other sources'."""
        found: dict[str, str] = {}
        with self.db.session(write=False) as session:
            rows = session.execute(
                select(EventRow.source, EventRow.title, ImageRow.url)
                .join(ImageRow, ImageRow.event_id == EventRow.id)
                .where(ImageRow.position == 0, ImageRow.downloaded_at.is_not(None))
                .order_by(EventRow.source == source)  # own source last, so it overwrites
            )
            for _, title, url in rows:
                if key := title_key(title):
                    found[key] = url
        return found

    def _save_batch(self, events: list[ScrapedEvent]) -> int:
        with self.db.session() as session:
            saved, orphaned_files = upsert_events(session, events)
            self.artists.record((name for e in events for name in e.lineup), session)  # one transaction
        self.media.delete_files(orphaned_files)
        return saved

    def _track_changes(self, source: str, run_id: int, status: str, found: int | None = None) -> None:
        """Record how many known events changed offers in this run; unlist the ones that vanished."""
        with self.db.session() as session:
            run = session.get(ScrapeRunRow, run_id)
            if run is None:
                return
            started = run.started_at
            run.events_changed = session.scalar(
                select(func.count()).select_from(EventRow).where(
                    EventRow.source == source, EventRow.offers_changed_at >= started, EventRow.first_seen_at < started
                )
            )
            run.events_unlisted = 0
            # Only a complete run proves an event is gone; a partial / failed / blocked one proves nothing,
            # nor does one that found nothing at all (a changed page layout reads as an empty listing).
            if status == "success" and found != 0 and source_settings(source, self.settings).unlist_missing:
                gone = session.scalars(
                    select(EventRow.id).where(EventRow.source == source, EventRow.last_seen_at < started, upcoming_filter())
                ).all()
                if gone:
                    session.execute(update(EventRow).where(EventRow.id.in_(gone)).values(is_listed=False))
                    record_delisted(session, source, gone)
                run.events_unlisted = len(gone)
                log.debug("source %s: %d events no longer listed", source, len(gone))

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


def title_key(title: str | None) -> str | None:
    """Show name for matching artwork across listings: "Name - Venue" and punctuation ignored."""
    key = re.sub(r"\W+", " ", (title or "").split(" - ")[0]).strip().casefold()
    return key if len(key) >= 4 else None


def fill_artwork(event: ScrapedEvent, known: dict[str, list[str]], by_title: dict[str, str]) -> None:
    """An event its source gives no artwork keeps what it had (e.g. before selling out),
    else borrows the artwork of another listing of the same show."""
    images = known.get(event.source_event_id) or ([url] if (url := by_title.get(title_key(event.title) or "")) else [])
    images = [u for u in images if not is_site_logo(u)]  # a logo stored by mistake is not kept
    if images:
        event.images, event.image_url = list(images), images[0]


def _finished_before(event: ScrapedEvent, settings: Settings) -> bool:
    """Same rule as display and retention (retention.is_current): over -> never stored."""
    return not is_current(event.start_at, event.end_at, settings.retention, settings.timezone, time_known=event.time_known)
