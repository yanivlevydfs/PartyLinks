"""Persistence, retention and API against a temporary SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, EventRow, ImageRow, upsert_events
from partylinks.enrichment import Enricher
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.retention import purge_expired


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database={"url": f"sqlite:///{tmp_path / 'test.db'}"},
        scheduler={"enabled": False},
        media={"download": False, "storage": "database"},
        geocoding={"enabled": True, "max_lookups_per_run": 0},  # never touch the network in tests
    )


def event(event_id: str, start: datetime, **extra: object) -> ScrapedEvent:
    fields: dict[str, object] = {
        "source": "test",
        "source_event_id": event_id,
        "url": f"https://example.com/{event_id}",
        "title": f"Techno night {event_id}",
        "start_at": start,
        "end_at": start + timedelta(hours=6),
        "address": "Tel Aviv-Yafo, Israel",
        "images": [f"https://example.com/{event_id}.jpg"],
        "tickets": [TicketTier(name="Free before 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(80))],
    }
    return ScrapedEvent(**(fields | extra))


def seed(settings: Settings, events: list[ScrapedEvent]) -> Database:
    db = Database(settings.database.url)
    db.create_schema()
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        upsert_events(session, [e for e in map(enricher.enrich, events) if e])
    return db


def test_retention_keeps_today_onwards_and_parties_still_in_progress(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(UTC)
    today = datetime.combine(now.astimezone(tz).date(), time.min, tzinfo=tz)
    db = seed(settings, [
        event("old", today - timedelta(days=4)),
        event("yesterday-show", today - timedelta(hours=3), end_at=today - timedelta(hours=1)),  # 21:00-23:00, over
        event("yesterday-no-end", today - timedelta(hours=3), end_at=None),  # the screenshot: no end time
        event("still-partying", now - timedelta(hours=3), end_at=now + timedelta(hours=3)),  # last night, still on
        event("season-listing", today - timedelta(days=20), end_at=now + timedelta(days=10)),  # far end date
        event("today", today + timedelta(hours=1)),
        event("future", now + timedelta(days=2)),
    ])  # fmt: skip

    purge_expired(db, MediaStore(settings.media, db), settings.retention, settings.timezone)

    with db.session() as session:
        remaining = {row.id for row in session.query(EventRow)}
        assert session.query(ImageRow).count() == len(remaining)  # purged events' artwork rows go too
    # still-partying is current either way: started today, or last night and not over yet.
    assert remaining == {"test:still-partying", "test:today", "test:future"}


def test_retention_shrinks_sqlite_file(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [event("old", datetime.now(UTC) - timedelta(days=4))])
    with db.session() as session:
        session.query(ImageRow).update({ImageRow.data: b"x" * 5_000_000, ImageRow.downloaded_at: datetime.now(UTC)})
    db.reclaim_space()
    before = db.storage_stats()["file_bytes"] or 0

    assert purge_expired(db, MediaStore(settings.media, db), settings.retention, settings.timezone) == 1

    after = db.storage_stats()
    assert (after["file_bytes"] or 0) < before // 5  # artwork bytes went back to the OS
    assert after["wal_bytes"] == 0


def test_api_filters_categories_and_stored_image(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("a", future), event("b", future, title="Hip hop night")])
    with db.session() as session:
        image = session.query(ImageRow).filter(ImageRow.event_id == "test:a").one()
        image.data, image.content_type, image.downloaded_at = b"\x89PNG", "image/png", datetime.now(UTC)
        image_id = image.id

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events", params={"genre": "techno", "entry_type": "free_and_paid"}).json()
        assert [e["id"] for e in page["items"]] == ["test:a"]

        detail = client.get("/events/test:a").json()
        assert [t["kind"] for t in detail["tickets"]] == ["free", "paid"]
        assert detail["images"][0]["stored_url"] == f"/images/{image_id}"

        stored = client.get(f"/images/{image_id}")
        assert stored.status_code == 200 and stored.content == b"\x89PNG"

        categories = client.get("/categories").json()
        assert {"value": "free_and_paid", "events": 2} in categories["entry_types"]
        assert client.get("/sources").status_code == 200


def test_seo_crawl_files_and_event_jsonld(tmp_path: Path) -> None:
    import json

    from partylinks.seo import event_jsonld

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("a", future, title="Techno </script> night")])

    with db.session() as session:
        row = session.query(EventRow).one()
        data = event_jsonld(row, settings)
    assert data["@type"] == "Event" or data["@type"] == "MusicEvent"
    assert data["startDate"].endswith(("+02:00", "+03:00"))  # Israel time
    assert {o["price"] for o in data["offers"]} == {0.0, 80.0}
    assert data["location"]["address"]["addressCountry"] == "IL"
    json.dumps(data, ensure_ascii=False, default=str)

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        robots = client.get("/robots.txt").text
        assert "Disallow: /admin" in robots and "Sitemap: http://localhost:8000/sitemap.xml" in robots
        sitemap = client.get("/sitemap.xml").text
        assert "/event/" in sitemap and 'hreflang="en"' in sitemap
        assert "Techno </script> night" in client.get("/llms.txt").text


def test_geocoding_reuses_known_places_and_rejects_vague_hits(tmp_path: Path) -> None:
    from partylinks.db import GeoCacheRow
    from partylinks.geo import Geocoder, candidates, street_key
    from partylinks.enrichment import Enricher

    assert street_key("דרך קיבוץ גלויות 52, תל אביב-יפו, ישראל") == "קיבוץ גלויות 52"
    assert street_key("אבן גבירול 30 קומה -1") == "אבן גבירול 30"
    assert street_key("בטבע היפה שלנו 🏕️") is None
    from partylinks.geo import venue_key
    assert venue_key("מועדון הבארבי") == venue_key("בארבי")
    assert venue_key("Jimmy who tel aviv") == venue_key("JIMMYWHO? Bar & Lounge")

    settings = settings_for(tmp_path)
    settings.geocoding.venues = {"gray club": (32.075175, 34.78205)}
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        event("barby", future, venue_name="בארבי", address="קיבוץ גלויות 52, תל אביב-יפו", latitude=32.0535, longitude=34.7744),
        event("selector", future, address="דרך קיבוץ גלויות 52, תל אביב-יפו, ישראל"),
        event("gray", future, venue_name="Gray Club תל אביב-יפו", address="אבן גבירול 30 קומה 1-"),
        event("hit", future, address="המרץ 2, תל אביב-יפו"),
        event("bnei-brak", future, address="Kibbutz Galuyot 52, Tel Aviv"),
        event("vague", future, address="Tel Aviv-Yafo, Israel"),
    ])  # fmt: skip
    with db.session() as session:
        rows = {r.source_event_id: r for r in session.query(EventRow)}
        cache = [
            GeoCacheRow(query=candidates(rows["hit"])[0], found=True, latitude=32.0519, longitude=34.7728, kind="amenity", city="תל־אביב–יפו"),
            GeoCacheRow(query=candidates(rows["bnei-brak"])[0], found=True, latitude=32.0754, longitude=34.8411, kind="road", city="בני ברק"),
            GeoCacheRow(query=candidates(rows["vague"])[0], found=True, latitude=32.0853, longitude=34.7818, kind="city", city="תל־אביב–יפו"),
        ]  # fmt: skip
        session.add_all(cache)

    enricher = Enricher(settings.enrichment, settings.timezone)
    geo = Geocoder(settings.geocoding, enricher.areas, settings.enrichment.target_city.aliases, db)
    stats = geo.locate_missing(network=False)

    with db.session() as session:
        located = {r.source_event_id: (r.latitude, r.area) for r in session.query(EventRow)}
    assert located["selector"] == (32.0535, "south")  # same street + number as Barby
    assert located["gray"][0] == 32.075175  # manual pin
    assert located["hit"][0] == 32.0519  # trusted cache hit
    assert located["bnei-brak"][0] is None  # wrong city
    assert located["vague"][0] is None  # city centroid is not a location
    assert stats.located == 3


def test_rescrape_tracks_offer_changes_and_unlists_vanished_events(tmp_path: Path) -> None:
    import time

    from partylinks.db import ScrapeRunRow, start_run
    from partylinks.service import ScrapeService

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    same = event("same", future, source="selector")
    price = event("price", future, source="selector")
    gone = event("gone", future, source="selector")
    db = seed(settings, [same, price, gone])
    with db.session() as session:
        first = {r.id: r.offers_changed_at for r in session.query(EventRow)}
    time.sleep(0.01)

    service = ScrapeService(settings, db)
    with db.session() as session:
        run_id = start_run(session, "selector", "test").id
    time.sleep(0.01)
    # Next scrape: "price" got a new tier price, "same" is unchanged, "gone" is no longer listed.
    changed = price.model_copy(update={"tickets": [TicketTier(name="Entry", price=Decimal(120))]})
    with db.session() as session:
        upsert_events(session, [same, changed])
    service._track_changes("selector", run_id, "success")

    with db.session() as session:
        rows = {r.source_event_id: r for r in session.query(EventRow)}
        run = session.get(ScrapeRunRow, run_id)
        assert rows["same"].offers_changed_at == first["selector:same"]
        assert rows["price"].offers_changed_at > first["selector:price"]
        assert [t.price for t in rows["price"].tickets] == [Decimal(120)]  # tiers replaced, not appended
        assert rows["gone"].is_listed is False and rows["same"].is_listed is True
        assert (run.events_changed, run.events_unlisted) == (1, 1)

    # Listed again -> back on the site.
    with db.session() as session:
        upsert_events(session, [gone])
        assert session.get(EventRow, "selector:gone").is_listed is True
    service.shutdown()
