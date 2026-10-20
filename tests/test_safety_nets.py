"""The safety nets, each driven through its actual failure:

1. shutdown stops a running source at the next event and still saves what it had;
2. a locked database is retried, other DB errors are not;
3. a batch the DB rejects falls back to row-by-row, saving every good row;
4. scheduled jobs swallow unexpected errors so the scheduler keeps running them.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from partylinks import scheduler as scheduler_module
from partylinks import service as service_module
from partylinks.config import HttpSettings, Settings
from partylinks.db import Database, EventRow, ScrapeRunRow
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.service import ScrapeService, _with_db_retry


def make_service(tmp_path: Path) -> tuple[ScrapeService, Database]:
    settings = Settings(
        database={"url": f"sqlite:///{tmp_path / 's.db'}"},
        scheduler={"enabled": False},
        media={"download": False},
        dedup={"enabled": False},
    )
    db = Database(settings.database.url)
    db.create_schema()
    return ScrapeService(settings, db), db


def event(eid: str) -> ScrapedEvent:
    return ScrapedEvent(
        source="goout", source_event_id=eid, url=f"https://e.example/{eid}", title=f"Party {eid}",
        start_at=datetime.now(UTC) + timedelta(days=2), tickets=[TicketTier(name="Entry", price=Decimal(50))],
    )  # fmt: skip


# ------------------------------------------------------------------ 1. shutdown flag
class _SlowSource(BaseScraper[SourceSettings]):
    """Yields one event, then waits until the service starts shutting down, then yields more."""

    name = "goout"
    settings_model = SourceSettings
    first_yielded = threading.Event()

    def __init__(self, stopping: threading.Event) -> None:
        super().__init__(SourceSettings(cron="0 * * * *"), HttpSettings(), "UTC")
        self._stopping = stopping

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        yield event("before-shutdown")
        self.first_yielded.set()
        while not self._stopping.is_set():
            await asyncio.sleep(0.01)
        for i in range(5):
            yield event(f"after-shutdown-{i}")


def test_shutdown_stops_a_running_source_and_keeps_what_it_collected(tmp_path: Path, monkeypatch: Any) -> None:
    service, db = make_service(tmp_path)
    monkeypatch.setattr(service_module, "build_scraper", lambda name, settings: _SlowSource(service._stopping))
    _SlowSource.first_yielded.clear()

    future = service.start_source("goout", trigger="test")
    assert _SlowSource.first_yielded.wait(timeout=10)
    service.shutdown()
    result = future.result(timeout=10)

    assert result.status == "interrupted"
    assert not service.is_running("goout")  # lock released
    with db.session() as session:
        assert [e.source_event_id for e in session.query(EventRow)] == ["before-shutdown"]
        assert session.query(ScrapeRunRow).one().status == "interrupted"


def test_shutdown_saves_even_when_the_thread_pool_is_closed(tmp_path: Path, monkeypatch: Any) -> None:
    """At interpreter exit asyncio.to_thread refuses work; the final save and run record still land."""
    service, db = make_service(tmp_path)
    monkeypatch.setattr(service_module, "build_scraper", lambda name, settings: _SlowSource(service._stopping))
    to_thread = asyncio.to_thread

    async def closed_after_shutdown(fn: Any, *args: Any) -> Any:
        if service._stopping.is_set():
            raise RuntimeError("cannot schedule new futures after shutdown")
        return await to_thread(fn, *args)

    monkeypatch.setattr(service_module.asyncio, "to_thread", closed_after_shutdown)
    _SlowSource.first_yielded.clear()

    future = service.start_source("goout", trigger="test")
    assert _SlowSource.first_yielded.wait(timeout=10)
    service.shutdown()

    assert future.result(timeout=10).status == "interrupted"
    with db.session() as session:
        assert [e.source_event_id for e in session.query(EventRow)] == ["before-shutdown"]
        assert session.query(ScrapeRunRow).one().status == "interrupted"


def test_no_new_runs_start_after_shutdown(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    service.shutdown()
    with pytest.raises(service_module.SourceBusyError):
        service.start_source("goout")


# ------------------------------------------------------------------ 2. locked-database retry
def test_locked_database_is_retried_until_it_frees(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module.time, "sleep", lambda seconds: None)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("UPDATE events", {}, Exception("database is locked"))
        return "saved"

    assert _with_db_retry("save", flaky) == "saved" and len(calls) == 3


def test_locked_database_gives_up_after_the_retry_budget(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module.time, "sleep", lambda seconds: None)
    calls: list[int] = []

    def always_locked() -> None:
        calls.append(1)
        raise OperationalError("UPDATE events", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        _with_db_retry("save", always_locked)
    assert len(calls) == service_module.DB_RETRIES


def test_other_database_errors_are_not_retried(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module.time, "sleep", lambda seconds: None)
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise OperationalError("SELECT", {}, Exception("no such table: events"))

    with pytest.raises(OperationalError):
        _with_db_retry("save", broken)
    assert len(calls) == 1


# ------------------------------------------------------------------ 3. row-by-row fallback
def test_rejected_batch_falls_back_to_row_by_row(tmp_path: Path, monkeypatch: Any) -> None:
    service, db = make_service(tmp_path)
    real_save_batch = service._save_batch

    def reject_bad_rows(events: list[ScrapedEvent]) -> int:
        if any(e.source_event_id == "bad" for e in events):  # what PostgreSQL does for one bad value
            raise IntegrityError("INSERT INTO events", {}, Exception("value too long for type character varying(96)"))
        return real_save_batch(events)

    monkeypatch.setattr(service, "_save_batch", reject_bad_rows)
    failures: list[str] = []
    saved = service._save([event("ok-1"), event("bad"), event("ok-2")], failures)

    assert saved == 2
    assert len(failures) == 1 and failures[0].startswith("goout:bad not saved: IntegrityError")
    with db.session() as session:
        assert sorted(e.source_event_id for e in session.query(EventRow)) == ["ok-1", "ok-2"]


# ------------------------------------------------------------------ 4. scheduled jobs keep running
class _BrokenService:
    async def run_source(self, source: str, trigger: str) -> None:
        raise RuntimeError("database exploded before the run row existed")

    async def purge_expired(self) -> int:
        raise RuntimeError("disk full during purge")


def test_scheduled_jobs_survive_unexpected_errors(caplog: Any) -> None:
    broken: Any = _BrokenService()
    asyncio.run(scheduler_module._scheduled_run(broken, "goout"))  # must not raise
    asyncio.run(scheduler_module._scheduled_retention(broken))  # must not raise
    assert "scheduled run of goout failed" in caplog.text and "scheduled retention failed" in caplog.text


def test_failed_retention_is_reported_for_health(tmp_path: Path, monkeypatch: Any) -> None:
    service, _ = make_service(tmp_path)

    def failing_purge(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("disk full during purge")

    monkeypatch.setattr(service_module, "purge_expired", failing_purge)
    with pytest.raises(RuntimeError):
        asyncio.run(service.purge_expired())
    assert service.retention_state.last_error == "RuntimeError: disk full during purge"
    assert not service.retention_running
    service.shutdown()


def test_event_without_artwork_keeps_or_borrows_one() -> None:
    from datetime import UTC, datetime

    from partylinks.models import ScrapedEvent
    from partylinks.service import fill_artwork, title_key

    def event(eid: str, title: str) -> ScrapedEvent:
        return ScrapedEvent(source="mevalim", source_event_id=eid, url="https://x.example", title=title,
                            start_at=datetime(2026, 11, 1, 20, tzinfo=UTC))  # fmt: skip

    assert title_key("יוסי וג'אגר - תיאטרון הבימה") == title_key("יוסי וג׳אגר") is not None
    assert title_key("A-1") is None  # too short to match safely
    by_title = {title_key("Yossi & Jagger"): "https://img/borrowed.jpg"}

    kept = event("1", "Yossi & Jagger - Habima")
    fill_artwork(kept, {"1": ["https://img/own.jpg"]}, by_title)
    assert kept.image_url == "https://img/own.jpg"  # its own earlier artwork wins

    borrowed = event("2", "Yossi & Jagger - Habima")
    fill_artwork(borrowed, {}, by_title)
    assert borrowed.images == ["https://img/borrowed.jpg"] == [borrowed.image_url]

    unknown = event("3", "Something else")
    fill_artwork(unknown, {}, by_title)
    assert unknown.images == [] and unknown.image_url is None
