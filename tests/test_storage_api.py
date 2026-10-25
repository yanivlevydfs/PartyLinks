"""Persistence, retention and API against a temporary SQLite database."""

from __future__ import annotations

import time as clock
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

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


def event(event_id: str, start: datetime, **extra: Any) -> ScrapedEvent:
    fields: dict[str, Any] = {
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
        stores = client.get("/ticket-sites").json()["stores"]
        assert stores and stores[0]["events"] > 0  # busiest first; enabled sources without events too, at 0
        assert {"goout", "airdrop"} <= {s["name"] for s in stores}
        assert "חנויות הכרטיסים" in client.get("/stores").text


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
        index = client.get("/sitemap.xml").text
        assert "<sitemapindex" in index and "/sitemap-1.xml" in index and "/sitemap-2.xml" not in index
        assert client.get("/sitemap-2.xml").status_code == 404 and client.get("/sitemap-0.xml").status_code == 404
        sitemap = client.get("/sitemap-1.xml").text
        assert "/event/" in sitemap and 'hreflang="en"' in sitemap
        # Privacy and cookie policy: crawlable, and its contact comes from /site.
        assert "/privacy" in sitemap and "מדיניות פרטיות" in client.get("/privacy").text
        assert "/terms" in sitemap and "אינם אחראים" in client.get("/terms").text
        assert "email" in client.get("/site").json()["privacy"]
        assert "/partners" in sitemap and "שיתופי פעולה" in client.get("/partners").text
        assert "Contact us" in client.get("/contact?lang=en").text
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
        assert "/genre/techno" in client.get("/sitemap-1.xml").text and "/tonight" in client.get("/").text
        city = client.get("/city/tel-aviv")  # "Tel Aviv-Yafo, Israel" was stored as the canonical "תל אביב"
        assert city.status_code == 200 and "מה עושים בתל אביב" in city.text
        assert client.get("/city/haifa").status_code == 404
        assert client.get("/genre/techno/weekend").status_code in (200, 404)  # depends on today's weekday
        assert client.get("/genre/techno/someday").status_code == 404
        assert "/city/tel-aviv" in client.get("/sitemap-1.xml").text
        # City x genre: its own page, linked from the city page (not the home page) and in the sitemap.
        city_techno = client.get("/city/tel-aviv/techno")
        assert city_techno.status_code == 200 and "טכנו בתל אביב" in city_techno.text and '"City"' in city_techno.text
        assert 'href="/city/tel-aviv/techno"' in city.text and "/city/tel-aviv/techno" in client.get("/sitemap-1.xml").text
        assert client.get("/city/tel-aviv/karaoke").status_code == 404 and client.get("/city/nowhere/techno").status_code == 404
        assert client.get("/region/sharon").status_code == 404 and client.get("/abroad/greece").status_code == 404
        info = client.get("/landing-info", params={"path": "/city/tel-aviv/techno", "lang": "en"}).json()
        assert info["title"] == "Techno events in Tel Aviv" and {"path": "/city/tel-aviv", "label": "All events in Tel Aviv"} in info["links"]


def test_city_names_are_canonical() -> None:
    enricher = Enricher(settings_for(Path(".")).enrichment, "Asia/Jerusalem")
    assert enricher.canonical_city("Tel Aviv-Yafo", None) == "תל אביב"
    assert enricher.canonical_city("תל אביב-יפו", None) == "תל אביב"
    assert enricher.canonical_city(None, "שדרות ירושלים 19, תל אביב-יפו, ישראל") == "תל אביב"  # street named after a city
    assert enricher.canonical_city("השרון", "השרון 14, תל אביב-יפו") == "תל אביב"
    assert enricher.canonical_city("השרון", None, "מייז תל אביב | MAZE TLV") == "תל אביב"
    assert enricher.canonical_city("6473303", None) is None  # zip code
    assert enricher.canonical_city("Mitzpe Ramon", None) == "מצפה רמון"  # any Israeli town (GeoNames)
    assert enricher.canonical_city("Kalamata", None) == "Kalamata"  # unknown place kept
    assert enricher.canonical_city("Eilat", None) == "אילת"


def test_geocoding_reuses_known_places_and_rejects_vague_hits(tmp_path: Path) -> None:
    from partylinks.db import GeoCacheRow
    from partylinks.enrichment import Enricher
    from partylinks.geo import Geocoder, candidates, street_key

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


def test_geocoder_drops_placeholder_and_out_of_city_source_points(tmp_path: Path) -> None:
    from partylinks.geo import Geocoder

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    fallback = {"latitude": 32.0681, "longitude": 34.7622}  # one point a source gives every venue it can't place
    db = seed(settings, [
        event("negev", future, venue_name="JIMMYWHO", latitude=31.046, longitude=34.852),  # centre of Israel
        *(event(f"fb{i}", future, venue_name=name, **fallback) for i, name in enumerate(["Humble", "Kuli Alma", "Radio EPGB", "Teder"])),
        event("real", future, venue_name="Barby", latitude=32.0535, longitude=34.7601),
        event("real2", future, venue_name="Barby", latitude=32.0535, longitude=34.7601),  # same venue twice is fine
    ])  # fmt: skip
    enricher = Enricher(settings.enrichment, settings.timezone)
    geo = Geocoder(settings.geocoding, enricher.areas, settings.enrichment.target_city.aliases, db, settings.enrichment.cities)
    assert geo.drop_bad_points() == 5
    with db.session() as session:
        placed = {r.source_event_id for r in session.query(EventRow) if r.latitude is not None}
    assert placed == {"real", "real2"}


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
        changed, before = rows["price"].offers_changed_at, first["selector:price"]
        assert changed is not None and before is not None and changed > before
        assert [t.price for t in rows["price"].tickets] == [Decimal(120)]  # tiers replaced, not appended
        assert rows["gone"].is_listed is False and rows["same"].is_listed is True
        assert run is not None and (run.events_changed, run.events_unlisted) == (1, 1)

    # Listed again -> back on the site.
    with db.session() as session:
        upsert_events(session, [gone])
        relisted = session.get(EventRow, "selector:gone")
        assert relisted is not None and relisted.is_listed is True
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
    (content / "gone.html").write_text("---\ntitle: t\ndescription: d\ndate: 2026-01-01\nexpires: 2026-01-02\n---\n<p>x</p>", encoding="utf-8")
    (content / "broken.html").write_text("---\ntitle: [\n---\n", encoding="utf-8")

    posts = blog.load_posts(settings, content)
    assert [p.slug for p in posts] == ["guide"]  # future-dated, expired and broken posts are hidden
    assert blog.toc(posts[0]) == [{"id": "הכי-טוב", "title": "הכי טוב"}]
    assert [list(b) for b in blog.blocks(posts[0], settings)] == [["html"], ["events"], ["html"]]
    assert blog.blocks(posts[0], settings)[1]["events"] == {"page_size": "2", "genre": "techno"}

    monkeypatch.setattr(blog, "CONTENT_DIR", content)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert client.get("/posts").json()["items"][0]["category"]["key"] == "music"
        assert client.get("/posts/guide").json()["faq"] == [{"q": "שאלה?", "a": "תשובה."}]
        assert client.get("/posts/later").status_code == 404
        assert "מדריך טכנו" in client.get("/blog/rss.xml").text
        assert "/blog/guide" in client.get("/sitemap-1.xml").text and "/blog/guide" in client.get("/llms.txt").text
        page = client.get("/blog/guide")
        if page.status_code != 404:  # server-rendered pages need the React build (web/dist)
            assert '"BlogPosting"' in page.text and '"FAQPage"' in page.text
            assert "Techno night 0" in page.text and 'hreflang="en"' not in page.text


def test_admin_scan_runs_all_enabled_or_the_chosen_sources(tmp_path: Path) -> None:
    from concurrent.futures import Future

    from partylinks.scrapers import configured_sources
    from partylinks.service import SourceBusyError

    settings = settings_for(tmp_path)
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as client:
        calls: list[str] = []

        def fake_start(name: str, trigger: str = "manual") -> Future:
            if name == "barby":
                raise SourceBusyError("barby is already running")
            calls.append(name)
            done: Future = Future()
            done.set_result(None)
            return done

        app.state.service.start_source = fake_start
        everything = client.post("/admin/api/scan").json()
        enabled = [n for n, s in configured_sources(settings).items() if s.enabled]
        assert set(everything["started"]) | set(everything["busy"]) == set(enabled) and everything["busy"] == ["barby"]
        calls.clear()
        assert client.post("/admin/api/scan", json={"sources": ["goout", "eventer"]}).json()["started"] == ["goout", "eventer"]
        assert calls == ["goout", "eventer"]
        assert client.post("/admin/api/scan", json={"sources": ["nope"]}).status_code == 404


def test_logging_is_info_by_default_and_debug_logs_everything(tmp_path: Path) -> None:
    import logging

    from partylinks.logs import debug_enabled, set_debug

    def access(status: int) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                 ("1.2.3.4:0", "GET", "/images/1", "1.1", status), None)  # fmt: skip

    settings = settings_for(tmp_path)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        log = logging.getLogger("uvicorn.access")
        assert not debug_enabled() and logging.getLogger().level == logging.INFO
        assert not log.filter(access(200)) and log.filter(access(404)) and log.filter(access(500))
        assert log.isEnabledFor(logging.INFO)

        assert client.post("/admin/api/logging", json={"on": True}).json() == {"debug": True}
        assert logging.getLogger().level == logging.DEBUG and log.filter(access(200))
        assert client.get("/admin/api/logging").json() == {"debug": True}
        assert client.post("/admin/api/logging", json={"on": False}).json() == {"debug": False}
        assert not log.filter(access(200))
    set_debug(False)


def test_bare_venue_names_take_their_city_from_other_events(tmp_path: Path) -> None:
    from partylinks.service import ScrapeService

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        event("addressed", future, venue_name="Ezra Bar", address="Ezra 5, Tel Aviv"),
        event("bare", future, venue_name="Ezra Bar", address=None),  # same venue, no address
        event("sarona", future, venue_name="Sarona Park", address=None),  # a Tel Aviv area keyword
        event("placed", future, venue_name="Somewhere", address=None, latitude=32.08, longitude=34.78),
        event("haifa", future, venue_name="Haifa Port Bar", address=None),
        event("unknown", future, venue_name="Nowhere", address=None),
    ])  # fmt: skip
    service = ScrapeService(settings, db)
    service.recompute_areas()
    with db.session() as session:
        rows = {r.source_event_id: (r.in_target_city, r.area) for r in session.query(EventRow)}
    service.shutdown()
    assert rows["bare"][0] is True and rows["sarona"] == (True, "business") and rows["placed"][0] is True
    assert rows["haifa"] == (False, "haifa") and rows["unknown"] == (None, None)


def test_filter_by_ticket_site_matches_any_listing_of_the_event(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        event("party", future, source="goout"),
        event("party-z", future, source="zygo"),  # the same party, also sold on Zygo
        event("other", future, source="goout", title="Hip hop night"),
    ])  # fmt: skip
    with db.session() as session:  # as dedup leaves them: one group, Go-Out's listing primary
        row = session.get(EventRow, "zygo:party-z")
        assert row is not None
        row.group_id, row.is_primary = "goout:party", False

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert [e["id"] for e in client.get("/events", params={"source": "zygo"}).json()["items"]] == ["goout:party"]
        sources = {f["value"]: f["events"] for f in client.get("/categories").json()["sources"]}
        assert sources == {"goout": 2, "zygo": 1}


def test_stored_events_abroad_get_a_country(tmp_path: Path) -> None:
    """Rows saved before countries were set: recompute moves them out of the Israeli feed."""
    from partylinks.service import ScrapeService

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        event("belfast", future, source="tiktik", venue_name="SSE Arena", address=None, city="Belfast"),
        event("rome", future, address="Roma, Italy", latitude=41.9, longitude=12.5),
        event("tlv", future),
    ])  # fmt: skip
    with db.session() as session:  # as stored by the old code: no country anywhere
        for row in session.query(EventRow):
            row.country = None
    service = ScrapeService(settings, db)
    service.recompute_areas()
    with db.session() as session:
        countries = {r.source_event_id: r.country for r in session.query(EventRow)}
    service.shutdown()
    assert countries == {"belfast": "ZZ", "rome": "ZZ", "tlv": None}


def test_umbrella_genre_includes_its_sub_genres(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.enrichment.genre_families = {"electronic": ["techno", "house"], "israeli": ["mizrahi"]}
    future = datetime.now(UTC) + timedelta(days=2)
    seed(settings, [
        event("techno", future),
        event("house", future, title="Deep house sunset"),
        event("hiphop", future, title="Hip hop night"),
        event("mizrahi", future, title="ערב מזרחית"),
    ])  # fmt: skip

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda genre: {e["id"] for e in client.get("/events", params={"genre": genre}).json()["items"]}
        assert ids("electronic") == {"test:techno", "test:house"}
        assert ids("techno") == {"test:techno"}  # a sub-genre stays narrow
        assert ids("israeli") == {"test:mizrahi"}
        counts = {g["value"]: g["events"] for g in client.get("/categories").json()["genres"]}
        assert counts["electronic"] == 2 and counts["israeli"] == 1


def test_only_committed_writes_count_as_data_changes(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [event("a", start)])
    before = Database.changes
    with db.session() as session:
        session.scalars(select(EventRow)).all()
    assert Database.changes == before
    with pytest.raises(RuntimeError), db.session() as session:
        session.execute(update(EventRow).values(title="rolled back"))
        raise RuntimeError
    assert Database.changes == before
    with db.session() as session:
        session.execute(update(EventRow).values(title="renamed"))
    assert Database.changes == before + 1


def test_cached_responses_follow_writes_and_map_returns_located_events(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [event("a", start, latitude=32.0636, longitude=34.7749)])
    with TestClient(create_app(settings)) as client:
        assert client.get("/events").json()["total"] == 1
        enricher = Enricher(settings.enrichment, settings.timezone)
        with db.session() as session:
            upsert_events(session, [e for e in [enricher.enrich(event("b", start, address=None))] if e])
        # The page cached before the write is served at once while it is rebuilt in the background.
        assert client.get("/events").json()["total"] == 1
        deadline = clock.monotonic() + 10
        while client.get("/events").json()["total"] != 2:
            assert clock.monotonic() < deadline, "stale page never rebuilt"
            clock.sleep(0.05)
        pins = client.get("/events/map").json()
        assert "test:a" in [p["id"] for p in pins["items"]] and len(pins["items"]) + pins["unplaced"] == 2
        assert set(pins["items"][0]) == {"id", "slug", "title", "start_at", "venue_name", "latitude", "longitude", "source", "sources"}


def test_seller_links_leave_only_through_the_rate_limited_redirect(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    seed(settings, [event("a", start, url="https://seller.example/a", extra={"purchase_url": "https://seller.example/buy"})])
    with TestClient(create_app(settings), headers={"user-agent": "Mozilla/5.0"}) as client:
        pages = [client.get(p).text for p in ("/events", "/events/test:a", "/events/map", "/changes", "/event/test:a")]
        assert not [p for p in pages if "seller.example" in p]
        assert client.get("/events/test:a").json()["url"] == "/go/test%3Aa"
        go = client.get("/go/test%3Aa?at=buy", follow_redirects=False)
        assert go.status_code == 302 and go.headers["location"].startswith("https://seller.example/a?utm_source=pulse&")
        assert "utm_content=buy" in go.headers["location"]
        client.get("/go/test%3Aa?at=alt", follow_redirects=False)
        client.get("/go/test%3Aa?at=nonsense", follow_redirects=False)
        client.get("/go/missing", follow_redirects=False)  # 404: not counted
        stats = client.get("/admin/api/clicks?days=7").json()
        assert stats["total"] == stats["today"] == stats["per_day"][-1]["clicks"] == 3
        assert len(stats["per_day"]) == 7
        assert stats["per_placement"] == {"buy": 1, "alt": 1, "unknown": 1}
        assert stats["top_events"] == [{"event_id": "test:a", "title": stats["top_events"][0]["title"], "source": "test", "clicks": 3}]
    limit = settings.security.go_rate_limit_per_minute
    with TestClient(create_app(settings), headers={"user-agent": "Mozilla/5.0"}, client=("203.0.113.9", 1)) as client:
        codes = [client.get("/go/test%3Aa", follow_redirects=False).status_code for _ in range(limit + 1)]
        assert codes[:limit] == [302] * limit and codes[-1] == 429


def test_geocoder_takes_a_venue_point_only_from_the_same_city(tmp_path: Path) -> None:
    from partylinks.geo import Geocoder

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [
        # the Cameri in Tel Aviv is placed; a touring "הקאמרי" show in Rishon must not take its point
        event("tlv", future, venue_name="תיאטרון הקאמרי", city="תל אביב", latitude=32.0790, longitude=34.7870),
        event("rishon", future, venue_name="תיאטרון הקאמרי", city="ראשון לציון"),
        # another Tel Aviv show of the Cameri does
        event("tlv2", future, venue_name="הקאמרי", city="תל אביב"),
        # a spelling in another city: no match ("סינמטק" in Tel Aviv is not "סינמטק הרצליה")
        event("hall", future, venue_name="תיאטרון הקאמרי אולם 1", city="ראשון לציון"),
    ])  # fmt: skip
    enricher = Enricher(settings.enrichment, settings.timezone)
    geo = Geocoder(settings.geocoding, enricher.areas, settings.enrichment.target_city.aliases, db, settings.enrichment.cities)
    geo.locate_missing(network=False)
    with db.session() as session:
        placed = {r.source_event_id: r.latitude for r in session.query(EventRow)}
    assert placed["rishon"] is None and placed["tlv2"] == 32.0790 and placed["hall"] is None


def test_geocoder_places_events_abroad_at_their_city(tmp_path: Path) -> None:
    from partylinks.db import GeoCacheRow, utcnow
    from partylinks.geo import Geocoder

    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    # Four venues in Berlin share the city point: not mistaken for a placeholder and dropped.
    db = seed(settings, [event(f"b{i}", future, venue_name=f"Club {i}", address=None, city="ברלין", country="DE") for i in range(4)])
    with db.session() as session:
        session.add(GeoCacheRow(query="ברלין @DE", found=True, latitude=52.52, longitude=13.4, kind="city", looked_up_at=utcnow()))
    enricher = Enricher(settings.enrichment, settings.timezone)
    geo = Geocoder(settings.geocoding, enricher.areas, settings.enrichment.target_city.aliases, db, settings.enrichment.cities)
    geo.locate_missing(network=False)
    geo.locate_missing(network=False)
    with db.session() as session:
        assert {r.latitude for r in session.query(EventRow)} == {52.52}


def test_web_city_list_mirrors_the_config() -> None:
    import re

    ts = (Path(__file__).parents[1] / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    block = ts.split("export const CITIES")[1].split("};")[0]
    web = dict(re.findall(r'^\s*"?([a-z-]+)"?: \{ he: "([^"]+)"', block, re.MULTILINE))
    assert web == {c.key: c.he for c in Settings().enrichment.cities}


def test_region_filter_mixes_tel_aviv_with_regions_and_locate_finds_them(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    seed(settings, [
        event("tlv", future, venue_name="Ezra Bar", address="Ezra 5, Tel Aviv"),
        event("haifa", future, venue_name="Haifa Port Bar", address=None),
        event("eilat", future, venue_name="Eilat Beach Bar", address="Eilat"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda qs: {e["id"].split(":")[1] for e in client.get(f"/events?{qs}").json()["items"]}
        assert ids("region=tel_aviv&region=haifa") == {"tlv", "haifa"}
        assert ids("region=eilat") == {"eilat"}
        # The facets follow the filters: the wizard's next question only offers what is left.
        regions = {f["value"] for f in client.get("/categories?region=haifa").json()["regions"]}
        assert regions == {"haifa"}
        assert client.get("/locate?lat=32.08&lng=34.78").json()["region"] == "tel_aviv"
        assert client.get("/locate?lat=32.80&lng=35.00").json() == {"region": "haifa", "city": "חיפה"}


def test_sponsored_rails_show_upcoming_picks_in_admin_order(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    seed(settings, [event(n, start) for n in ("a", "b", "c", "d")])
    with TestClient(create_app(settings)) as client:
        assert client.get("/events/sponsored").json() == {"right": [], "left": []}
        picks = [
            {"event_id": "test:b", "side": "right"},
            {"event_id": "test:a", "side": "left"},
            {"event_id": "test:c", "side": "right"},
            {"event_id": "test:d", "side": "left", "until": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
        ]
        assert client.put("/admin/api/sponsored", json=picks).json() == {"saved": 4}
        assert client.get("/events/sponsored").json() == {"right": [], "left": []}  # switched off by default
        assert client.post("/admin/api/sponsored/enabled", json={"on": True}).json() == {"enabled": True}
        rails = client.get("/events/sponsored").json()
        assert [e["id"] for e in rails["right"]] == ["test:b", "test:c"]
        assert [e["id"] for e in rails["left"]] == ["test:a"]  # d's campaign is over
        listed = client.get("/admin/api/sponsored").json()
        assert listed["enabled"] is True
        assert [(s["event_id"], s["live"]) for s in listed["items"]] == [
            ("test:a", True), ("test:d", False), ("test:b", True), ("test:c", True),
        ]
        assert client.put("/admin/api/sponsored", json=[{"event_id": "test:nope", "side": "left"}]).status_code == 404
        assert client.put("/admin/api/sponsored", json=picks[:1] * 2).status_code == 422
        assert client.get("/events/test:a").status_code == 200  # the slug route still works
        hit = lambda event_id, kind: client.post("/events/sponsored/hit", json={"event_id": event_id, "kind": kind})
        assert hit("test:b", "view").status_code == 204
        hit("test:b", "view"), hit("test:b", "click"), hit("test:c", "view")
        hit("test:nope", "click"), hit("test:b", "nonsense")  # unknown event / kind: not counted
        report = client.get("/admin/api/sponsored/stats?days=7").json()
        assert (report["views"], report["clicks"]) == (3, 1)
        assert [(e["event_id"], e["views"], e["clicks"]) for e in report["events"]] == [("test:b", 2, 1), ("test:c", 1, 0)]
        client.post("/admin/api/sponsored/enabled", json={"on": False})
        assert client.get("/events/sponsored").json() == {"right": [], "left": []}  # every ad hidden
        hit("test:b", "click")  # nothing on show: not counted
        assert client.get("/admin/api/sponsored/stats").json()["clicks"] == 1


def test_busy_towns_outside_the_config_get_city_pages(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    start = datetime.combine(datetime.now(tz).date(), time(21, 0), tzinfo=tz) + timedelta(days=1)
    shoham = [event(f"s{i}", start + timedelta(days=i), address="שהם, ישראל") for i in range(10)]
    yavne = [event(f"y{i}", start + timedelta(days=i), address="יבנה, ישראל") for i in range(5)]
    seed(settings, [*shoham, *yavne])

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/city/shoham")  # a GeoNames town with 10 upcoming events
        assert page.status_code == 200 and "מה עושים בשהם" in page.text
        assert client.get("/city/yavne").status_code == 404  # 5 events: too few for a town page
        assert "/city/shoham" in client.get("/sitemap-1.xml").text
        assert client.get("/landing-info", params={"path": "/city/shoham"}).json()["city"]["he"] == "שהם"


def test_stored_events_take_genres_added_to_the_config(tmp_path: Path) -> None:
    from partylinks.service import ScrapeService

    settings = settings_for(tmp_path)
    db = seed(settings, [event("a", datetime.now(UTC) + timedelta(days=1), title="SINNERS FETISH PLAY PARTY")])
    with db.session() as session:  # scraped before the genre existed
        session.execute(update(EventRow).values(genres=[]))
    service = ScrapeService(settings, db)
    try:
        assert service.recompute_genres() == 1
        with db.session(write=False) as session:
            assert "kink" in session.scalars(select(EventRow.genres)).one()
        assert service.recompute_genres() == 0  # same config: nothing re-read
    finally:
        service.shutdown()


def test_artist_page_lists_their_shows_abroad_too(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("rome", future, address="Roma, Italy", latitude=41.9, longitude=12.5), event("tlv", future)])
    with db.session() as session:  # lineups come from curated artists only: set it as stored
        for row in session.query(EventRow):
            row.lineup = ["flashcookie"] if row.source_event_id == "rome" else []
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda q: [e["source_event_id"] for e in client.get(f"/events?{q}").json()["items"]]  # noqa: E731
        assert ids("artist=flashcookie") == ["rome"]
        assert ids("artist=flashcookie&abroad=false") == []
        assert "rome" not in ids("")  # the main feed stays in Israel


def test_smart_search_reads_places_genres_prices_and_covers_abroad(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    pricey = [TicketTier(name="Entry", price=Decimal(120))]
    seed(settings, [
        event("rome", future, address="Roma, Italy", latitude=41.9, longitude=12.5, tickets=pricey),
        event("tlv", future, tickets=pricey),
        event("haifa", future, title="Jazz club", address="Haifa, Israel", tickets=[TicketTier(name="Entry", price=Decimal(50))]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        ids = lambda q: sorted(e["source_event_id"] for e in client.get("/events", params={"q": q}).json()["items"])  # noqa: E731
        assert ids("techno") == ["rome", "tlv"]  # search covers abroad too
        assert ids("טכנו roma") == ["rome"]
        assert ids("ג׳אז בחיפה") == ids("jazz haifa") == ["haifa"]
        assert ids("night עד 100") == []
        assert ids("עד 100") == ["haifa"]
        assert ids("abroad") == ["rome"]
        assert ids("nothing like this") == []


def test_abroad_filters_come_from_the_stored_events(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    seed(settings, [
        event("berlin", future, title="Berlin night", city="ברלין", country="DE", address=None),
        event("paris", future, title="Paris night", city="פריז", country="FR", address=None),
        event("later", future + timedelta(days=10), title="Paris later", city="פריז", country="FR", address=None),
        event("home", future, title="Tel Aviv night"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        facets = client.get("/categories?abroad=true").json()
        assert {f["value"]: f["events"] for f in facets["countries"]} == {"FR": 2, "DE": 1}
        cities = client.get("/categories?abroad=true&country=FR").json()["cities"]
        assert [c["value"] for c in cities] == ["פריז"]
        until = (future + timedelta(days=1)).date().isoformat()
        page = client.get(f"/events?abroad=true&city=פריז&date_to={until}T23:59:59").json()
        assert [e["title"] for e in page["items"]] == ["Paris night"]


def test_geocoder_never_asks_for_just_the_town_or_a_generic_hall_name() -> None:
    from types import SimpleNamespace

    from partylinks.geo import candidates

    def queries(venue: str, city: str | None) -> list[str]:
        return candidates(SimpleNamespace(venue_name=venue, city=city, address=None, in_target_city=None))

    # "רעננה, רעננה" answered with a park: every Ra'anana hall landed there, then was cleared, every run.
    assert queries("רעננה", "רעננה") == []
    # Unknown city: "היכל התרבות" + the Tel Aviv default put other cities' halls on Tel Aviv's.
    assert queries("היכל התרבות", None) == []
    assert "היכל התרבות, תל אביב" not in queries("היכל התרבות מעלה אדומים", None)
    # With its city, or a name that carries one, a hall is specific.
    assert queries("היכל התרבות", "רעננה") == ["היכל התרבות, רעננה"]
    assert "מרכז המוסיקה רעננה, רעננה" in queries("מרכז המוסיקה רעננה", "רעננה")


def test_one_complex_under_many_names_is_not_a_placeholder_point() -> None:
    from partylinks.geo import distinct_venues, venue_key

    # Real production points (Oct 2026): halls and spellings of one place, cleared every pass before.
    for names in (
        ["תיאטרון ירושלים", "תאטרון ירושלים אולם שרובר", "אולם שרובר", "אולם שרובר תיאטרון ירושלים"],
        ["המשכן לאמנויות הבמה באר שבע", "אולם 1 המשכן לאמנויות הבמה באר שבע", "בית האופרה המשכן לאמנויות הבמה"],
        ["קאמרי", "תיאטרון הקאמרי", "תיאטרון הקאמרי אולם 1", "תיאטרון הקאמרי אולם 3"],
    ):
        assert distinct_venues({venue_key(n) or "" for n in names}) == 1, names
    # A real placeholder (one point sent for unrelated clubs) still counts each.
    assert distinct_venues({venue_key(n) or "" for n in ("בארבי", "גגרין", "הבלוק", "לבונטין 7")}) == 4
