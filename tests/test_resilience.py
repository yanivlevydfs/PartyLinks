"""Resilience: failures stay local, nothing collected is lost, bad config disables one source."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from partylinks.api import create_app
from partylinks.config import HttpSettings, Settings
from partylinks.db import Database, EventRow, ImageRow, ScrapeRunRow, bulk_update_by_id, upsert_events
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, from_unix, parse_iso
from partylinks.scrapers import configured_sources, source_errors
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.service import ScrapeService


# ------------------------------------------------------------------ transport / listing helpers
class _Probe(BaseScraper[SourceSettings]):
    name = "probe"
    settings_model = SourceSettings

    async def scrape(self):  # type: ignore[override]
        if False:
            yield


def probe(handler: Any) -> _Probe:
    scraper = _Probe(SourceSettings(cron="0 * * * *"), HttpSettings(request_delay_seconds=0, backoff_seconds=0), "UTC")
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return scraper


def test_failing_listing_page_is_skipped_not_fatal() -> None:
    async def fetch(index: int) -> tuple[list[int], int | None]:
        if index == 2:
            raise RuntimeError("page 2 broke")
        return list(range(index * 10, index * 10 + 10)), 40

    scraper = probe(lambda r: httpx.Response(200))
    items = asyncio.run(scraper.paginate(fetch, page_size=10, max_pages=10))
    assert len(items) == 30 and 25 not in items  # pages 0, 1, 3 kept
    assert scraper.failures and "listing page 2" in scraper.failures[0]


def test_string_total_from_api_is_accepted() -> None:
    async def fetch(index: int) -> tuple[list[int], Any]:
        return list(range(10)), "25"  # some APIs send counts as strings

    assert len(asyncio.run(probe(lambda r: httpx.Response(200)).paginate(fetch, 10, 10))) == 30


def test_html_page_instead_of_json_is_a_parse_error() -> None:
    scraper = probe(lambda r: httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"}))
    with pytest.raises(ParseError, match="expected JSON"):
        asyncio.run(scraper.get_json("https://x.example/api"))
    scraper = probe(lambda r: httpx.Response(200, content=b"null", headers={"content-type": "application/json"}))
    with pytest.raises(ParseError, match="expected dict"):
        asyncio.run(scraper.get_json("https://x.example/api", expect=dict))


def test_retry_after_is_honoured_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1 else httpx.Response(200, json={})

    assert asyncio.run(probe(handler).get_json("https://x.example/api")) == {} and len(calls) == 2


def test_stream_isolated_skips_bad_items_and_stops_on_block() -> None:
    scraper = probe(lambda r: httpx.Response(200))

    async def fn(item: int) -> int:
        if item == 3:
            raise ValueError("bad item")
        return item * 2

    async def collect(items: list[int], fn: Any) -> list[int]:
        return [x async for x in scraper.stream_isolated(items, fn, label=str)]

    assert sorted(asyncio.run(collect([1, 2, 3, 4], fn))) == [2, 4, 8]

    async def blocked(item: int) -> int:
        raise SourceBlockedError("checkpoint")

    with pytest.raises(SourceBlockedError):
        asyncio.run(collect([1, 2], blocked))


def test_odd_scalars_never_raise() -> None:
    assert parse_iso("20/10/2026 22:00") is None and parse_iso(1700000000) is None
    assert parse_iso("2026-10-20T22:00:00", "Not/AZone").tzinfo is not None
    assert from_unix(1_794_506_430_000) == from_unix(1_794_506_430)  # milliseconds


# ------------------------------------------------------------------ config
def test_one_invalid_source_block_disables_only_that_source() -> None:
    settings = Settings()
    settings.sources["goout"] = {**settings.sources["goout"], "cron": "15 */3 * *"}  # 4 fields
    settings.sources["zygo"] = None  # all keys commented out
    valid = configured_sources(settings)
    assert "goout" not in valid and "zygo" not in valid and "eventer" in valid
    assert set(source_errors(settings)) == {"goout", "zygo"}


# ------------------------------------------------------------------ persistence
def db_for(tmp_path: Path) -> tuple[Settings, Database]:
    settings = Settings(
        database={"url": f"sqlite:///{tmp_path / 'r.db'}"},
        scheduler={"enabled": False},
        media={"download": False, "storage": "filesystem", "directory": str(tmp_path / "media")},
    )
    db = Database(settings.database.url)
    db.create_schema()
    return settings, db


def event(eid: str, **extra: object) -> ScrapedEvent:
    fields: dict[str, object] = {
        "source": "test", "source_event_id": eid, "url": f"https://e.example/{eid}", "title": f"Party {eid}",
        "start_at": datetime.now(UTC) + timedelta(days=2), "images": ["https://img.example/flyer.jpg"],
        "tickets": [TicketTier(name="Entry", price=Decimal(50))],
    }  # fmt: skip
    return ScrapedEvent(**(fields | extra))


def test_bulk_update_skips_rows_deleted_meanwhile(tmp_path: Path) -> None:
    _, db = db_for(tmp_path)
    with db.session() as session:
        upsert_events(session, [event("a")])
    with db.session() as session:  # "b" vanished (e.g. retention) - must not raise StaleDataError
        bulk_update_by_id(session, EventRow, [{"id": "test:a", "group_size": 3}, {"id": "test:b", "group_size": 3}])
    with db.session() as session:
        assert session.get(EventRow, "test:a").group_size == 3  # type: ignore[union-attr]


def test_shared_artwork_file_survives_deleting_one_event(tmp_path: Path) -> None:
    settings, db = db_for(tmp_path)
    media = MediaStore(settings.media, db)
    shared = "test/flyer.jpg"
    media.path_for(shared).parent.mkdir(parents=True)
    media.path_for(shared).write_bytes(b"jpg")
    with db.session() as session:
        upsert_events(session, [event("a"), event("b")])
        session.execute(text("UPDATE event_images SET local_path = :p, downloaded_at = CURRENT_TIMESTAMP"), {"p": shared})
    with db.session() as session:
        session.execute(text("DELETE FROM event_images WHERE event_id = 'test:a'"))
    assert media.delete_files([shared]) == 0 and media.path_for(shared).exists()  # still used by "b"
    with db.session() as session:
        session.execute(text("DELETE FROM event_images"))
    assert media.delete_files([shared]) == 1 and not media.path_for(shared).exists()


def test_migration_backfills_defaults_for_existing_rows(tmp_path: Path) -> None:
    _, db = db_for(tmp_path)
    with db.session() as session:
        upsert_events(session, [event("a")])
    with db.engine.begin() as conn:  # simulate a DB created before these columns existed
        conn.execute(text("ALTER TABLE events DROP COLUMN group_size"))
    db.create_schema()
    with db.session() as session:
        assert session.get(EventRow, "test:a").group_size == 1  # type: ignore[union-attr]


def test_stuck_running_runs_are_marked_interrupted(tmp_path: Path) -> None:
    settings, db = db_for(tmp_path)
    with db.session() as session:
        session.add(ScrapeRunRow(source="test", trigger="schedule", status="running", started_at=datetime.now(UTC)))
    ScrapeService(settings, db).shutdown()  # startup recovery
    with db.session() as session:
        assert [r.status for r in session.query(ScrapeRunRow)] == ["interrupted"]


def test_events_that_started_before_yesterday_are_never_shown(tmp_path: Path) -> None:
    settings, db = db_for(tmp_path)
    long_running = event(
        "season", start_at=datetime.now(UTC) - timedelta(days=20), end_at=datetime.now(UTC) + timedelta(days=10)
    )
    with db.session() as session:
        upsert_events(session, [long_running, event("next")])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert [e["id"] for e in client.get("/events").json()["items"]] == ["test:next"]
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["checks"]["database"] == "ok"
        assert client.get("/events", params={"q": "100%_off"}).status_code == 200  # wildcards escaped
        assert client.get("/events", params={"genre": ["x"] * 50}).status_code == 422  # bounded params
