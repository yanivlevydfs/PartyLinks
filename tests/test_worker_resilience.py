"""The worker's resilience fixes, each driven through its failure:

1. a failed Nominatim request is no answer: nothing cached as "not found", the pass stops asking;
2. artwork downloads use the proxy the run ended on, not the pool's first (often dead) one;
3. a run whose setup fails (artist book) is recorded as failed, never left "running";
4. the supervisor keeps restarting the worker when starting the process itself fails;
5. the heartbeat keeps beating while long passes fill the loop's default executor, and a background
   step that raises is logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from partylinks.config import HttpSettings, SchedulerSettings
from partylinks.db import Database, GeoCacheRow, ScrapeRunRow
from partylinks.enrichment import Enricher
from partylinks.geo import Geocoder
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.worker import runner, supervisor
from partylinks.worker.link import WorkerLink
from tests.test_safety_nets import make_service
from tests.test_storage_api import event, seed, settings_for


# ------------------------------------------------------------------ 1. geocoding
def test_a_failed_geocoding_request_is_not_cached_and_ends_the_pass(tmp_path: Path, monkeypatch: Any) -> None:
    settings = settings_for(tmp_path)
    settings.geocoding.max_lookups_per_run = 5
    settings.geocoding.min_interval_seconds = 1
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("a", future, address="המרץ 2, תל אביב-יפו"), event("b", future, address="הרצל 10, תל אביב-יפו")])
    asked: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(429, request=request)  # Nominatim rationing us

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(refuse), **kw))
    enricher = Enricher(settings.enrichment, settings.timezone)
    geo = Geocoder(settings.geocoding, enricher.areas, settings.enrichment.target_city.aliases, db, settings.enrichment.cities)
    stats = geo.locate_missing(network=True)

    assert len(asked) == 1 and stats.lookups == 1  # stopped after the first refusal
    with db.session() as session:  # and "not found" for 14 days it is not
        assert session.scalar(select(func.count()).select_from(GeoCacheRow)) == 0


# ------------------------------------------------------------------ 2. artwork proxy
class _Proxied(BaseScraper[SourceSettings]):
    name = "goout"
    settings_model = SourceSettings

    async def scrape(self):  # type: ignore[no-untyped-def]  # pragma: no cover - never scraped
        yield None


def test_artwork_goes_through_the_proxy_the_run_ended_on() -> None:
    async def scenario() -> tuple[str | None, str | None]:
        scraper = _Proxied(SourceSettings(cron="0 * * * *"), HttpSettings(source_proxy="http://dead:1,http://good:2"), "UTC")
        async with scraper:
            assert scraper.working_http.proxy == "http://dead:1"
            scraper._next_proxy(scraper._client, httpx.ConnectError("refused"))  # type: ignore[arg-type]
            return scraper.http.proxy, scraper.working_http.proxy

    first, working = asyncio.run(scenario())
    assert first == "http://dead:1" and working == "http://good:2"


# ------------------------------------------------------------------ 3. setup failure
def test_a_run_whose_setup_fails_is_recorded_not_left_running(tmp_path: Path, monkeypatch: Any) -> None:
    service, db = make_service(tmp_path)

    def broken() -> Any:
        raise RuntimeError("artist book unreadable")

    monkeypatch.setattr(service.artists, "load", broken)
    result = service.start_source("goout", trigger="test").result(timeout=30)
    service.shutdown()
    assert result.status == "failed" and "artist book" in (result.error or "")
    with db.session() as session:
        run = session.get(ScrapeRunRow, result.run_id)
        assert run is not None and run.status == "failed" and run.finished_at is not None


# ------------------------------------------------------------------ 4. supervisor
def test_the_supervisor_retries_when_the_process_cannot_start(monkeypatch: Any) -> None:
    starts: list[int] = []
    stopped = threading.Event()

    class FlakyProcess:
        pid = 1

        def __init__(self, args: list[str]) -> None:
            starts.append(1)
            if len(starts) == 1:
                raise OSError("Cannot allocate memory")

        def wait(self, timeout: float | None = None) -> int:
            stopped.wait(timeout)
            return 0

        def poll(self) -> int | None:
            return 0 if stopped.is_set() else None

        def terminate(self) -> None:
            stopped.set()

        kill = terminate

    monkeypatch.setattr(supervisor.subprocess, "Popen", FlakyProcess)
    process = supervisor.WorkerProcess(SchedulerSettings(restart_backoff_max_seconds=0.01))
    process.start()
    deadline = time.monotonic() + 5
    while len(starts) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    process.stop()
    assert len(starts) == 2  # the thread survived the failed start and started it again


# ------------------------------------------------------------------ 5. heartbeat
def test_the_heartbeat_beats_while_the_default_executor_is_busy(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = settings_for(tmp_path)
    settings.scheduler.heartbeat_seconds = 0.05
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    link = WorkerLink(db, settings.scheduler)
    release = threading.Event()

    def fails() -> None:
        raise ValueError("variants exploded")

    async def scenario() -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        worker = runner.Worker(settings, db)
        await loop.run_in_executor(worker._beat_thread, worker._publish)
        before = (await loop.run_in_executor(worker._beat_thread, link.state) or {})["heartbeat_at"]
        busy = asyncio.ensure_future(asyncio.to_thread(release.wait, 10))  # a long pass holds the only thread
        worker._spawn(loop.run_in_executor(worker._beat_thread, fails))
        beat = asyncio.ensure_future(worker._beat())
        await asyncio.sleep(0.5)
        after = (await loop.run_in_executor(worker._beat_thread, link.state) or {})["heartbeat_at"]
        beat.cancel()
        release.set()
        await busy
        worker.service.shutdown()
        return before.timestamp(), after.timestamp()

    with caplog.at_level(logging.ERROR, logger="partylinks.worker.runner"):
        before, after = asyncio.run(scenario())
    assert after > before  # published although the default executor never freed up
    assert "background step failed" in caplog.text and "variants exploded" in caplog.text

