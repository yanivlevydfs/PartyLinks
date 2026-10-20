"""Persistence, retention and API against a temporary SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

import pytest
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


def test_seo_landing_pages_with_live_faq(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    tonight = datetime.combine(datetime.now(tz).date(), time(23, 30), tzinfo=tz)
    seed(settings, [event(str(i), tonight + timedelta(days=i)) for i in range(3)])

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        techno = client.get("/genre/techno")
        assert techno.status_code == 200 and '"FAQPage"' in techno.text
        assert "כרגע יש 3 אירועים" in techno.text and "כן, 3 אירועים בכניסה חופשית" in techno.text  # answers come from the listings
        assert client.get("/genre/karaoke").status_code == 404  # no events: no thin page
        assert "Techno night 0" in client.get("/tonight?lang=en").text
        assert "/genre/techno" in client.get("/sitemap.xml").text and "/tonight" in client.get("/").text
        city = client.get("/city/tel-aviv")  # "Tel Aviv-Yafo, Israel" was stored as the canonical "תל אביב"
        assert city.status_code == 200 and "מה עושים בתל אביב" in city.text
        assert client.get("/city/haifa").status_code == 404
        assert client.get("/genre/techno/weekend").status_code in (200, 404)  # depends on today's weekday
        assert client.get("/genre/techno/someday").status_code == 404
        assert "/city/tel-aviv" in client.get("/sitemap.xml").text


def test_city_names_are_canonical() -> None:
    enricher = Enricher(settings_for(Path(".")).enrichment, "Asia/Jerusalem")
    assert enricher.canonical_city("Tel Aviv-Yafo", None) == "תל אביב"
    assert enricher.canonical_city("תל אביב-יפו", None) == "תל אביב"
    assert enricher.canonical_city(None, "שדרות ירושלים 19, תל אביב-יפו, ישראל") == "תל אביב"  # street named after a city
    assert enricher.canonical_city("השרון", "השרון 14, תל אביב-יפו") == "תל אביב"
    assert enricher.canonical_city("השרון", None, "מייז תל אביב | MAZE TLV") == "תל אביב"
    assert enricher.canonical_city("6473303", None) is None  # zip code
    assert enricher.canonical_city("Eilat", None) == "Eilat"  # unknown city kept


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
    from partylinks.geo import venue_variants
    assert "מוזיאון תל אביב" in venue_variants("מוזיאון תל אביב, אולם אסיא")
    assert "הוואנה" in venue_variants("הוואנה מיוזיק קלאב")
    assert "אולם" not in venue_variants("אולם ברטונוב")  # a generic word alone would match any hall
    assert not any(v.endswith(" תל") for v in venue_variants("גריי תל אביב"))

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


def test_other_cities_are_purged_when_focusing_on_tel_aviv(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        event("tlv", future),
        event("haifa", future, address="Herzl 1, Haifa, Israel"),
        event("unknown", future, address=None),  # no location info: city unknown, kept
    ])  # fmt: skip
    media = MediaStore(settings.media, db)
    purge_expired(db, media, settings.retention, settings.timezone, drop_other_cities=True)
    with db.session() as session:
        assert {row.id for row in session.query(EventRow)} == {"test:tlv", "test:unknown"}


def test_blog_posts_pages_feed_and_live_embeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from partylinks import blog

    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    tonight = datetime.combine(datetime.now(tz).date(), time(23, 30), tzinfo=tz)
    seed(settings, [event(str(i), tonight + timedelta(days=i)) for i in range(3)])
    content = tmp_path / "blog"
    content.mkdir()
    (content / "guide.html").write_text(
        '---\ntitle: "מדריך טכנו"\ndescription: "תיאור"\ndate: 2026-01-01\ncategory: music\n'
        'faq:\n  - q: "שאלה?"\n    a: "תשובה."\n---\n<h2>הכי טוב</h2><p>שלום</p>\n<!--events genre=techno limit=2-->\n<p>סוף</p>',
        encoding="utf-8",
    )
    (content / "later.html").write_text("---\ntitle: t\ndescription: d\ndate: 2999-01-01\n---\n<p>x</p>", encoding="utf-8")
    (content / "broken.html").write_text("---\ntitle: [\n---\n", encoding="utf-8")

    posts = blog.load_posts(settings, content)
    assert [p.slug for p in posts] == ["guide"]  # future-dated and broken posts are hidden
    assert blog.toc(posts[0]) == [{"id": "הכי-טוב", "title": "הכי טוב"}]
    assert [list(b) for b in blog.blocks(posts[0], settings)] == [["html"], ["events"], ["html"]]
    assert blog.blocks(posts[0], settings)[1]["events"] == {"page_size": "2", "genre": "techno"}

    monkeypatch.setattr(blog, "CONTENT_DIR", content)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert client.get("/posts").json()["items"][0]["category"]["key"] == "music"
        assert client.get("/posts/guide").json()["faq"] == [{"q": "שאלה?", "a": "תשובה."}]
        assert client.get("/posts/later").status_code == 404
        assert "מדריך טכנו" in client.get("/blog/rss.xml").text
        assert "/blog/guide" in client.get("/sitemap.xml").text and "/blog/guide" in client.get("/llms.txt").text
        page = client.get("/blog/guide")
        if page.status_code != 404:  # server-rendered pages need the React build (web/dist)
            assert '"BlogPosting"' in page.text and '"FAQPage"' in page.text
            assert "Techno night 0" in page.text and 'hreflang="en"' not in page.text
