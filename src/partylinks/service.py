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
import re
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DataError, IntegrityError

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
from partylinks.places import all_cities
from partylinks.retention import is_current, purge_expired
from partylinks.scrapers import SCRAPERS, build_scraper, source_settings
from partylinks.scrapers.base import SourceBlockedError
from partylinks.venues import venue_updates

log = logging.getLogger(__name__)

SAVE_BATCH_SIZE = 200


PROXY_RETRY, PROXY_RETRY_AFTER_S = "proxy-retry", 600
# Errors of a dead or refused proxy (not of the site's content).
_PROXY_ERRORS = ("ProxyError", "ConnectError", "ConnectTimeout", "RemoteProtocolError", "ReadError", "SOCKS", "on every proxy",
                 "timed out after",
                 "403 Forbidden")  # refused on every proxy of the pool: other IPs later  # the run limit spent walking dead proxies (tiuli, barak, issta, movieland)


def _proxy_failure(result: RunResult) -> bool:
    if result.status == "partial" and not result.events_saved and result.events_failed:
        return True  # every page failed (livevents: each month on a dead proxy) and nothing was saved
    return result.status in ("failed", "blocked") and any(m in (result.error or "") for m in _PROXY_ERRORS)


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
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.enricher = Enricher(settings.enrichment, settings.timezone)
        self.artists = ArtistBook(db)
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

        ponytail: ~9 ms an event (~4 min at 28k), read outside the write lock; a scrape that saves an
        event meanwhile may be overwritten with its older text's genres until its next run.
        """
        fields = {"genres", "declared_genres", "genre_families", "detect_genres_from_text", "text_ignore",
                  "talk_keywords", "talk_genres", "declared_text_genres"}  # fmt: skip
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
            area = matcher.match(row.venue_name, row.address, row.latitude, row.longitude, here, city)
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
        log.info("areas/cities: updated %d events", changed)
        return changed

    def regroup(self) -> int:
        """Recompute duplicate groups across all sources (serialized between source threads)."""
        with self._dedup_lock:
            return _with_db_retry("regroup", self._regroup_once)

    def _regroup_once(self) -> int:
        # Whole-table passes over ~40k rows: computed in read sessions, the write lock only for the
        # changed rows (it was held for minutes and starved every save, Oct 2026). ponytail: a row
        # saved between a read and its write is regrouped on the next pass.
        with self.db.session(write=False) as session:
            renames = venue_updates(session)  # before grouping: a shared venue helps match duplicates
        if renames:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, renames)
        log.info("venues: renamed %d events", len(renames))
        with self.db.session(write=False) as session:
            merged, changed = self.deduplicator.plan(session)
        if changed:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, changed)
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
        try:
            result = asyncio.run(self._run(source, trigger))
        finally:
            lock.release()
        if trigger != PROXY_RETRY and _proxy_failure(result) and not self._stopping.is_set():
            # Its proxies died mid-run: once more later, on others (the dead ones go last, see base.py).
            log.info("source %s failed on its proxies: running it again in %d min", source, PROXY_RETRY_AFTER_S // 60)
            timer = threading.Timer(PROXY_RETRY_AFTER_S, self._retry_source, (source,))
            timer.daemon = True
            timer.start()
        return result

    def _retry_source(self, source: str) -> None:
        if self._stopping.is_set():
            return
        try:
            self.start_source(source, PROXY_RETRY)
        except SourceBusyError:
            pass  # already running again (schedule or admin): that run is the retry

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
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.exception("retention failed")
                raise
            state.last_success_at, state.last_removed, state.last_error = utcnow(), removed, None
            return removed

    async def _run(self, source: str, trigger: str) -> RunResult:
        run_id = await asyncio.to_thread(_with_db_retry, "start run", lambda: self._create_run(source, trigger))
        known, blocked = await asyncio.to_thread(self.artists.load)
        profiles = await asyncio.to_thread(self.artists.profiles)
        self.enricher.learn_artists(known, blocked, featured=[p.name for p in profiles.values()])
        found = saved = 0
        status, error = "success", None
        scraper = None
        failures: list[str] = []
        batch: dict[str, ScrapedEvent] = {}
        try:
            own = source_settings(source, self.settings).run_timeout_seconds
        except Exception:  # noqa: BLE001 - an unknown or broken source fails below, with its own error
            own = None
        timeout = own or self.settings.scheduler.run_timeout_seconds

        async def flush() -> None:
            nonlocal saved
            if batch:
                events = list(batch.values())
                batch.clear()
                saved += await self._blocking(self._save, events, failures)

        deadline = asyncio.timeout(timeout)
        try:
            async with deadline:
                scraper = build_scraper(source, self.settings)
                scraper.known_images = await asyncio.to_thread(self._known_images, source)
                scraper.linked_urls = await asyncio.to_thread(self._linked_urls, source)
                artwork = await asyncio.to_thread(self._artwork_by_title, source)
                scraper.known_artists = {key: p for key, p in profiles.items() if p.source == source}
                scraper.wanted = lambda event: not self.enricher.is_dropped(event)
                failures = scraper.failures
                async with scraper:
                    try:
                        async for event in scraper.scrape():
                            if self._stopping.is_set():
                                raise _StopRequested
                            found += 1
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

        # Post-processing: outside the scrape timeout; failures only downgrade to partial.
        if saved > 0 and scraper is not None and not self._stopping.is_set():
            if self.settings.media.download:
                await self._post_step(source, "artwork", failures, self.media.download_pending(source, scraper.http))
            if self.settings.dedup.enabled:
                await self._post_step(source, "regroup", failures, asyncio.to_thread(self._regroup_soon))
            # Saving overwrote coordinates with what the source sent; restore / look up the rest.
            await self._post_step(source, "geocode", failures, asyncio.to_thread(self._geocode_soon))
        if scraper is not None and scraper.artist_profiles and not self._stopping.is_set():  # lineups, next run on
            await self._post_step(
                source, "artist profiles", failures, asyncio.to_thread(self.artists.save_profiles, scraper.artist_profiles)
            )
        if status == "success" and failures:
            status = "partial"

        if not self._stopping.is_set():  # shutting down: the run is recorded as interrupted on next start
            try:
                await asyncio.to_thread(_with_db_retry, "track changes", lambda: self._track_changes(source, run_id, status))
            except Exception:
                log.exception("source %s: change tracking failed", source)

        result = RunResult(run_id, source, status, found, saved, len(failures), error)
        try:
            await self._blocking(_with_db_retry, "finish run", lambda: self._finish_run(result, failures))
        except Exception:
            log.exception("source %s: could not record run %s", source, run_id)
        log.info("source %s finished: %s", source, result)
        return result

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

    def _track_changes(self, source: str, run_id: int, status: str) -> None:
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
            # Only a complete run proves an event is gone; a partial / failed / blocked one proves nothing.
            if status == "success" and source_settings(source, self.settings).unlist_missing:
                gone = session.scalars(
                    select(EventRow.id).where(EventRow.source == source, EventRow.last_seen_at < started, upcoming_filter())
                ).all()
                if gone:
                    session.execute(update(EventRow).where(EventRow.id.in_(gone)).values(is_listed=False))
                    record_delisted(session, source, gone)
                run.events_unlisted = len(gone)
                log.info("source %s: %d events no longer listed", source, len(gone))

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
    if images:
        event.images, event.image_url = list(images), images[0]


def _finished_before(event: ScrapedEvent, settings: Settings) -> bool:
    """Same rule as display and retention (retention.is_current): over -> never stored."""
    return not is_current(event.start_at, event.end_at, settings.retention, settings.timezone)
