"""Place info: reading HappyTLV, Ontopo and own-site pages; matching a place to a venue; the page and its SEO."""

from __future__ import annotations

import json
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.db import Database
from partylinks.placeinfo.config import PlaceInfoSettings
from partylinks.placeinfo.models import OntopoVenueRow, PlaceInfoRow
from partylinks.placeinfo.pages import (
    happytlv_cards,
    happytlv_details,
    ontopo_page,
    ontopo_sitemap,
    phone_key,
    site_details,
    weekly,
)
from partylinks.placeinfo.refresh import Place, Venue, _public_host, _Reader, match_places, run_pass
from partylinks.scrapers.happytlv import parse_happy_hours
from partylinks.venues import PLACE_LISTING
from tests.test_storage_api import event, seed, settings_for

CFG = PlaceInfoSettings()
BAR = "https://www.happytlv.com/tel-aviv-happy-hours/yan"


def _ld(node: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


def _spec(days: list[str], opens: str, closes: str) -> dict:
    return {"@type": "OpeningHoursSpecification", "dayOfWeek": [f"https://schema.org/{d}" for d in days], "opens": opens, "closes": closes}


PLACE = {
    "@type": "BarOrPub", "name": "YAN", "telephone": "03-794-3529",
    "geo": {"latitude": 32.0842657, "longitude": 34.7741235},
    "address": {"streetAddress": "בן גוריון 40", "addressLocality": "תל אביב"},
    "sameAs": ["https://www.yansushi.co.il/", "https://www.instagram.com/yan_tlv"],
    "priceRange": "₪₪",
    "openingHoursSpecification": [_spec(["Sunday", "Monday"], "12:00", "23:59"), _spec(["Friday"], "18:00", "23:00"), _spec(["Friday"], "12:00", "16:00")],
    "amenityFeature": [{"name": "ישיבה בחוץ", "value": True}, {"name": "חניה", "value": False}],
    "makesOffer": {"name": "האפי האוור ב-YAN", "description": "25% על שתייה", "itemOffered": {"hoursAvailable": [_spec(["Sunday"], "17:00", "19:00")]}},
}  # fmt: skip
FAQ = {"@type": "FAQPage", "mainEntity": [
    {"@type": "Question", "name": "מה ההנחה בהאפי האוור של YAN?", "acceptedAnswer": {"text": "25% על האלכוהול. בצהריים 10% על הכל."}},
]}  # fmt: skip
PAGE = (
    '<html><head><meta name="description" content="YAN: ★ 4.4 בגוגל (3,184 ביקורות). שעות פתיחה."></head><body>'
    '<div class="vsub"><span>הצפון הישן</span><span class="rt">★ 4.4</span></div>'
    '<a href="https://ontopo.com/he/il/page/20214686?source=happyTLV">book</a>'
    '<a href="https://maps.google.com/?cid=15976620052474297928&g_mp=abc">map</a>'
    '<a href="https://ontopo.com/he/il/page/not-a-slug">x</a>'
    f"{_ld(PLACE)}{_ld(FAQ)}</body></html>"
)


def test_happytlv_page_gives_what_the_listing_used_to_drop() -> None:
    d = happytlv_details(PLACE, PAGE)
    assert d["hours"] == {"Sunday": ["12:00-23:59"], "Monday": ["12:00-23:59"], "Friday": ["12:00-16:00", "18:00-23:00"]}
    assert d["deal_hours"] == {"Sunday": ["17:00-19:00"]}
    assert d["deal_note"] == "25% על האלכוהול. בצהריים 10% על הכל."
    assert (d["rating"], d["reviews"], d["hood"]) == (4.4, 3184, "הצפון הישן")
    assert d["ontopo_slug"] == "20214686"
    assert d["maps_url"] == "https://maps.google.com/?cid=15976620052474297928"
    assert d["amenities"] == ["ישיבה בחוץ"]  # false ones left out
    assert d["links"] == PLACE["sameAs"]


def test_happytlv_page_without_those_gives_nothing_extra() -> None:
    assert happytlv_details({"name": "x"}, "<html><body></body></html>") == {}


def test_happy_hour_listing_carries_the_details() -> None:
    from datetime import date

    events = parse_happy_hours(PAGE, BAR, "Asia/Jerusalem", date(2026, 11, 8), 1)  # a Sunday
    assert events[0].extra["rating"] == 4.4 and events[0].extra["happy_hour"] is True


def test_hub_cards_tags() -> None:
    hub = '<article class="vcard" data-slug="yan" data-tags="דייט|חברים"></article><article class="vcard" data-slug="x"></article>'
    assert happytlv_cards(hub) == {"yan": {"tags": ["דייט", "חברים"]}}


def _ontopo(page: dict, items: list | None = None) -> str:
    state = {"mainStore": {}, "websiteContentStore": {"pageData": page, "pageContent": {"details": {"items": items or []}}}}
    # The real page has `undefined` values, which JSON doesn't.
    return f"<html><head><script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)[:-1]},\"x\":undefined}};(function(){{}})();</script>"


def test_ontopo_page() -> None:
    shift = {"title": "יום שני", "hours": [{"hour": "12:00 - 16:00"}, {"hour": "16:00 - 17:00"}, {"hour": "19:00 - 00:00"}, {"hour": "סגור", "disabled": True}]}
    html = _ontopo(
        {"title": "YAN", "geolocation": {"coordinates": [34.77, 32.08]}, "phone": "037943529", "address": "בן גוריון 40", "website": "www.yan.co.il",
         "tag1": "סושי", "tag2": "אסייאתי ", "gallery": [{"image": "https://c/1.jpg"}]},
        [{"icon": "phone"}, {"openingHours": {"shifts": [shift]}}],
    )  # fmt: skip
    d = ontopo_page(html)
    assert d is not None
    assert (d["title"], d["latitude"], d["longitude"], d["phone"]) == ("YAN", 32.08, 34.77, "037943529")
    assert d["tags"] == ["סושי", "אסייאתי"] and "photos" not in d  # Ontopo's terms: no copying its images
    assert d["hours"] == {"Monday": ["12:00-17:00", "19:00-00:00"]}  # a menu change is no closing
    assert ontopo_page("<html></html>") is None
    assert ontopo_page(_ontopo({})) == {}  # a page with no venue


def test_ontopo_sitemap_takes_israeli_pages_once() -> None:
    xml = """<urlset><url><loc>https://ontopo.com/he/il/page/111</loc><lastmod>2025-07-08</lastmod></url>
    <url><loc>https://ontopo.com/en/il/page/111</loc></url><url><loc>https://ontopo.com/sr/rs/page/222</loc></url>
    <url><loc>https://ontopo.com/he/il/tel-aviv</loc></url><url><loc>https://ontopo.com/he/il/page/333</loc></url></urlset>"""
    assert ontopo_sitemap(xml) == {"111": "2025-07-08", "333": None}


def test_phone_key() -> None:
    assert phone_key("03-794-3529") == phone_key("+972 3 7943529") == "037943529"
    assert phone_key("*2700") is None and phone_key(None) is None


def test_weekly_skips_what_it_cannot_read() -> None:
    assert weekly([{"dayOfWeek": "Sunday"}, "x"]) is None
    assert weekly({"dayOfWeek": "Sunday", "opens": "09:00:00", "closes": "17:00:00"}) == {"Sunday": ["09:00-17:00"]}


def test_own_site_jsonld() -> None:
    page = _ld({"@type": "WebSite", "name": "x"}) + _ld(
        {"@type": "Restaurant", "telephone": "03-1", "address": {"streetAddress": "דיזנגוף 1"},
         "openingHoursSpecification": [_spec(["Monday"], "10:00", "20:00")]}
    )  # fmt: skip
    assert site_details(page) == {"hours": {"Monday": ["10:00-20:00"]}, "phone": "03-1", "address": "דיזנגוף 1"}
    assert site_details("<html></html>") is None


def test_match_order_and_false_matches() -> None:
    venues = [
        Venue("phone", "רוסו", 32.0843, 34.7741, "037943529"),
        Venue("branch-far", "רוסו", 32.5, 34.9, "037943529"),
        Venue("name", "מסעדת הים הגדול", 32.0700, 34.7700, None),
        Venue("next-door", "בית קפה אחר לגמרי", 32.0601, 34.7601, None),
        Venue("same-owner", "NOEMA", 32.1000, 34.8000, "035555555"),
    ]
    places = [
        Place("link", "ROSSO", 32.0, 34.0, None, slug="given"),  # the page's own link wins, whatever is near
        Place("phone", "ROSSO", 32.0842, 34.7741, "037943529"),  # Latin and Hebrew meet through phone + sound
        Place("far-phone", "ROSSO", 31.0, 35.0, "037943529"),  # a chain's phone, far from every branch
        Place("name", "מסעדת הים הגדול", 32.0701, 34.7700, "0500000000"),
        Place("no-name", "בר שכן", 32.0601, 34.7601, None),  # next door, another name
        Place("no-pin", "רוסו", None, None, "037943529"),
        Place("owner-phone", "מונרו", 32.1000, 34.8000, "035555555"),  # one restaurateur's phone, another place
    ]
    assert match_places(places, venues, CFG) == {
        "link": ("given", "link"), "phone": ("phone", "phone"), "name": ("name", "name"),
    }  # fmt: skip


def test_own_sites_inside_our_network_are_refused() -> None:
    reader = _Reader(CFG.model_copy(update={"delay_seconds": 0}), threading.Event())
    reader.client.event_hooks = {"request": [_public_host]}
    assert reader.get("http://127.0.0.1:9/") is None and reader.failures == 1


def _place_row(when: datetime, **extra: object) -> object:
    return event(
        "hh-1", when, title="האפי האוור ב-YAN", url=BAR, end_at=when + timedelta(hours=2), venue_name="YAN", address="בן גוריון 40",
        latitude=32.0842, longitude=34.7741, source_genres=["happy hour"],
        extra={PLACE_LISTING: True, "deal": "25%", "phone": "03-794-3529", "ontopo_slug": "20214686", **extra},
    )  # fmt: skip


def test_place_page_card_and_seo(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    when = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time(18), tz)
    seed(settings, [_place_row(
        when, rating=4.4, reviews=3184, hood="הצפון הישן", hours={"Sunday": ["12:00-23:59"]}, tags=["דייט", "דיל חזק"],
        links=["https://www.yan.co.il", "https://www.instagram.com/yan"],
    )])  # fmt: skip
    db = Database(settings.database.url, options=settings.database)
    with db.session() as session:
        session.add(PlaceInfoRow(url=BAR, ontopo_slug="20214686", matched_by="link"))
        session.add(OntopoVenueRow(slug="20214686", title="YAN", tags=["סושי"], hours={"Monday": ["10:00-11:00"]}))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        card = client.get("/events").json()["items"][0]
        assert (card["rating"], card["reviews"], card["hood"], card["bookable"]) == (4.4, 3184, "הצפון הישן", True)
        assert client.get(f"/events/{card['id']}").json()["place"] is True

        info = client.get(f"/places/info/{card['id']}", params={"lang": "en"}).json()
        assert info["hours"] == {"Sunday": ["12:00-23:59"]} and info["hours_from"] == "happytlv"  # Ontopo's only fills gaps
        assert info["ontopo_url"] == "https://ontopo.com/en/il/page/20214686?source=spotx"
        assert info["website"] == "https://www.yan.co.il" and info["instagram"] == "https://www.instagram.com/yan"
        assert info["cuisine"] == ["סושי"] and info["tags"] == ["דייט"]  # deal mechanics are not shown
        assert "photos" not in info
        assert client.get("/places/info/nothing").status_code == 410

        page = client.get(f"/event/{card['slug']}", headers={"user-agent": "Googlebot"}).text
        assert '"LocalBusiness"' in page or '"BarOrPub"' in page
        assert "MusicEvent" not in page and "aggregateRating" not in page  # Google: no third-party ratings in markup
        assert "12:00-23:59" in page and "ontopo.com" in page


def test_pass_reads_matches_and_keeps_going(tmp_path: Path, monkeypatch: object) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    when = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time(18), tz)
    seed(settings, [_place_row(when, links=["https://own.example"])])
    db = Database(settings.database.url, options=settings.database)
    pages = {
        CFG.ontopo_sitemap: b"<loc>https://ontopo.com/he/il/page/20214686</loc><lastmod>2026-01-01</lastmod><loc>https://ontopo.com/he/il/page/777</loc>",
        "https://ontopo.com/he/il/page/20214686": _ontopo({"title": "YAN", "gallery": [{"image": "https://c/1.jpg"}, {"image": "https://c/2.jpg"}]}).encode(),
        "https://own.example": _ld({"@type": "Restaurant", "telephone": "03-1"}).encode(),
    }  # fmt: skip
    asked: list[str] = []

    def get(self: _Reader, url: str, limit: int | None = None) -> bytes | None:
        asked.append(url)
        return pages.get(url)  # 777 fails

    monkeypatch.setattr(_Reader, "get", get)  # type: ignore[attr-defined]
    cfg = CFG.model_copy(update={"delay_seconds": 0})
    run_pass(db, cfg, threading.Event())
    with db.session(write=False) as session:
        assert session.get(OntopoVenueRow, "20214686").lastmod == "2026-01-01"
        assert session.get(OntopoVenueRow, "777") is None
        place = session.get(PlaceInfoRow, BAR)
        assert (place.ontopo_slug, place.matched_by, place.site) == ("20214686", "link", {"phone": "03-1"})
        assert place.site_checked_at is not None

    asked.clear()
    run_pass(db, cfg, threading.Event())  # a second pass reads nothing it has: the sitemap, then what failed
    assert "https://ontopo.com/he/il/page/20214686" not in asked and "https://own.example" not in asked
    assert "https://ontopo.com/he/il/page/777" in asked


def test_page_ontopo_answers_with_500_is_not_asked_every_pass(tmp_path: Path, monkeypatch: object) -> None:
    """Five such pages in a row ended every pass (max_consecutive_failures) before any other page was read."""
    settings = settings_for(tmp_path)
    seed(settings, [])
    db = Database(settings.database.url, options=settings.database)
    broken = "https://ontopo.com/he/il/page/12874624"
    pages = {CFG.ontopo_sitemap: b"<loc>https://ontopo.com/he/il/page/12874624</loc><lastmod>2026-01-01</lastmod>"}
    asked: list[str] = []

    def get(self: _Reader, url: str, limit: int | None = None) -> bytes | None:
        asked.append(url)
        self.status = 500 if url == broken else 200
        return pages.get(url)

    monkeypatch.setattr(_Reader, "get", get)  # type: ignore[attr-defined]
    cfg = CFG.model_copy(update={"delay_seconds": 0})
    run_pass(db, cfg, threading.Event())
    with db.session(write=False) as session:
        assert session.get(OntopoVenueRow, "12874624").lastmod == "2026-01-01"
    asked.clear()
    run_pass(db, cfg, threading.Event())
    assert broken not in asked
