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
from partylinks.db import Database, EventRow, ScrapeRunRow, bulk_update_by_id, upsert_events
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
    fallback = parse_iso("2026-10-20T22:00:00", "Not/AZone")
    assert fallback is not None and fallback.tzinfo is not None
    assert from_unix(1_794_506_430_000) == from_unix(1_794_506_430)  # milliseconds


# ------------------------------------------------------------------ config
def test_one_invalid_source_block_disables_only_that_source() -> None:
    settings = Settings()
    settings.sources["goout"] = {**(settings.sources["goout"] or {}), "cron": "15 */3 * *"}  # 4 fields
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


def event(eid: str, **extra: Any) -> ScrapedEvent:
    fields: dict[str, Any] = {
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


def test_current_event_rule_at_2am() -> None:
    from zoneinfo import ZoneInfo

    from partylinks.retention import is_current

    s, tz = Settings().retention, "Asia/Jerusalem"
    at = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=ZoneInfo(tz))
    now = at(20, 2)  # Tuesday 02:00
    assert not is_current(at(19, 21), None, s, tz, now)  # yesterday's 21:00 show (the screenshot): over
    assert not is_current(at(19, 21), at(19, 23), s, tz, now)  # ended before midnight: over
    assert is_current(at(19, 23), at(20, 5), s, tz, now)  # last night's party still going: kept until 05:00
    assert not is_current(at(19, 23), at(20, 5), s, tz, at(20, 6))  # ...and gone after it ends
    assert not is_current(at(1, 22), at(30, 4), s, tz, now)  # "season" listing that started weeks ago
    assert is_current(at(20, 22), None, s, tz, now)  # tonight


def test_artwork_run_with_successes_and_failures_persists_both(tmp_path: Path, monkeypatch: Any) -> None:
    settings, db = db_for(tmp_path)
    settings.media.storage = "database"
    with db.session() as session:
        upsert_events(session, [event("a", images=["https://img.example/ok.jpg", "https://img.example/broken.jpg"])])
    media = MediaStore(settings.media, db)

    async def fake_fetch(client: Any, url: str) -> tuple[bytes, str]:
        if "broken" in url:
            raise ValueError("not an image")
        return b"\xff\xd8\xffjpeg", "image/jpeg"

    monkeypatch.setattr(media, "_fetch", fake_fetch)
    stored, failed = asyncio.run(media.download_pending("test", HttpSettings()))
    assert (stored, failed) == (1, 1)
    with db.session() as session:
        rows = {r.url: (r.downloaded_at is not None, r.error) for r in session.execute(text("select url, downloaded_at, error from event_images"))}
    assert rows["https://img.example/ok.jpg"] == (True, None)
    assert rows["https://img.example/broken.jpg"][0] is False and "not an image" in rows["https://img.example/broken.jpg"][1]


def test_source_proxy_reaches_every_source_unless_opted_out() -> None:
    http = HttpSettings(source_proxy="http://p:1")
    default = _Probe(SourceSettings(cron="0 * * * *"), http, "UTC")
    opted_out = _Probe(SourceSettings(cron="0 * * * *", proxied=False), http, "UTC")
    assert default.http.proxy == "http://p:1" and opted_out.http.proxy is None
    assert _Probe(SourceSettings(cron="0 * * * *"), HttpSettings(), "UTC").http.proxy is None  # local: direct


def test_proxy_pool_moves_on_when_the_proxy_fails() -> None:
    http = HttpSettings(source_proxy="http://u:p@a:1, http://u:p@b:2")
    scraper = _Probe(SourceSettings(cron="0 * * * *", proxied=True), http, "UTC")
    assert scraper.http.proxy == "http://u:p@a:1" and scraper._spare_proxies == ["http://u:p@b:2"]

    async def run() -> None:
        async with scraper:
            first = scraper._client
            assert first is not None
            scraper._next_proxy(first, httpx.ProxyError("CONNECT 500"))
            second = scraper._client
            assert second is not None and second is not first and scraper._spare_proxies == []
            scraper._next_proxy(first, httpx.ProxyError("late failure of the old client"))
            assert scraper._client is second  # parallel failures of one client move on once
            scraper._next_proxy(second, httpx.ProxyError("pool spent"))
            assert scraper._client is second  # nothing left: stays, the retries then give up
        assert scraper._client is None and scraper._retired_clients == []

    asyncio.run(run())


def test_refused_proxy_ip_moves_to_the_next_proxy() -> None:
    http = HttpSettings(source_proxy="http://a:1, http://b:2", backoff_seconds=0)
    scraper = _Probe(SourceSettings(cron="0 * * * *"), http, "UTC")
    seen: list[str | None] = []

    def client(proxy: str | None) -> httpx.AsyncClient:
        seen.append(proxy)
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403 if proxy == "http://a:1" else 200)))

    scraper._new_client = client  # type: ignore[method-assign]

    async def run() -> int:
        async with scraper:
            first = (await scraper.request("GET", "https://site.example/")).status_code
            # Pool spent: the next refusal is the site's answer, not retried again and again.
            scraper._client = client("http://a:1")
            with pytest.raises(httpx.HTTPStatusError):
                await scraper.request("GET", "https://site.example/")
            return first

    assert asyncio.run(run()) == 200
    assert seen[:2] == ["http://a:1", "http://b:2"]


def test_artwork_in_the_database_moves_to_files_and_is_still_served(tmp_path: Path) -> None:
    settings, db = db_for(tmp_path)
    with db.session() as session:
        upsert_events(session, [event("a", images=["https://img.example/a.jpg", "https://img.example/b.png"])])
        session.execute(
            text("UPDATE event_images SET data = :d, content_type = 'image/jpeg', downloaded_at = CURRENT_TIMESTAMP"),
            {"d": b"\xff\xd8\xffjpeg"},
        )
    settings.media.storage = "filesystem"
    media = MediaStore(settings.media, db)
    assert media.move_to_files(batch=1) == 2  # one per batch: resumes after a restart
    assert media.move_to_files() == 0
    with db.session() as session:
        rows = session.execute(text("SELECT id, data, local_path FROM event_images")).all()
    assert all(data is None and path and media.path_for(path).read_bytes() == b"\xff\xd8\xffjpeg" for _, data, path in rows)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        served = client.get(f"/images/{rows[0][0]}")
    assert served.status_code == 200 and served.content == b"\xff\xd8\xffjpeg"


def test_mostly_empty_database_file_is_compacted(tmp_path: Path) -> None:
    _, db = db_for(tmp_path)
    with db.session() as session:
        upsert_events(session, [event("a", images=["https://img.example/a.jpg"])])
        session.execute(text("UPDATE event_images SET data = :d"), {"d": b"x" * 4_000_000})
    with db.session() as session:
        session.execute(text("UPDATE event_images SET data = NULL"))
    before = db.storage_stats()
    assert db.compact_if_bloated(min_free_bytes=1_000_000) > 0
    after = db.storage_stats()
    assert (after["file_bytes"] or 0) < (before["file_bytes"] or 0) and after["free_bytes"] == 0
    assert db.compact_if_bloated(min_free_bytes=1_000_000) == 0  # nothing left to do


def test_a_source_can_have_a_longer_run_limit() -> None:
    from partylinks.scrapers import source_settings

    settings = Settings()
    assert source_settings("tiktik", settings).run_timeout_seconds == 3600
    assert source_settings("goout", settings).run_timeout_seconds is None  # the scheduler's default


def test_a_timeout_is_retried_the_usual_times_not_pool_size_more() -> None:
    http = HttpSettings(source_proxy="http://a:1, http://b:2, http://c:3, http://d:4", max_retries=1, backoff_seconds=0)
    scraper = _Probe(SourceSettings(cron="0 * * * *"), http, "UTC")
    tries: list[str | None] = []

    def client(proxy: str | None) -> httpx.AsyncClient:
        def timeout(request: httpx.Request) -> httpx.Response:
            tries.append(proxy)
            raise httpx.ReadTimeout("slow site", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(timeout))

    scraper._new_client = client  # type: ignore[method-assign]

    async def run() -> None:
        async with scraper:
            await scraper.request("GET", "https://slow.example/")

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(run())
    assert len(tries) == 2  # max_retries + 1, on the same proxy (a read timeout is the site, not the proxy)


def test_israel_proxy_sources_try_israeli_ips_first_then_the_usual_pool() -> None:
    http = HttpSettings(source_proxy="http://pool:1", israel_proxy="http://il1:1, http://il2:2")
    scraper = _Probe(SourceSettings(cron="0 * * * *", israel_proxy=True), http, "UTC")
    assert {scraper.http.proxy, scraper._spare_proxies[0]} == {"http://il1:1", "http://il2:2"}
    assert scraper._spare_proxies[-1] == "http://pool:1"
    plain = _Probe(SourceSettings(cron="0 * * * *"), http, "UTC")
    assert plain.http.proxy == "http://pool:1" and plain._spare_proxies == []


def test_israel_proxy_feed_goes_first_and_is_cached(monkeypatch) -> None:
    from partylinks.scrapers import base

    calls = []
    rows = {"data": [{"ip": "1.2.3.4", "port": "80", "protocols": ["https"]}, {"ip": "5.6.7.8", "port": "1080", "protocols": ["socks5"]}, {"ip": "9.9.9.9", "port": "4145", "protocols": ["socks4"]}]}
    monkeypatch.setattr(base.httpx, "get", lambda url, timeout: calls.append(url) or httpx.Response(200, json=rows, request=httpx.Request("GET", url)))
    base._feed_cache.clear()
    http = HttpSettings(israel_proxy_feed="http://feed", israel_proxy="http://il1:1")
    for _ in range(2):
        scraper = _Probe(SourceSettings(cron="0 * * * *", israel_proxy=True), http, "UTC")
    assert {scraper.http.proxy, *scraper._spare_proxies} == {"http://1.2.3.4:80", "socks5://5.6.7.8:1080", "http://il1:1"}  # socks4 skipped
    assert calls == ["http://feed"]
