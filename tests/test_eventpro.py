"""EventPro community-centre box offices: listing cards and event pages, on real (utf-8 saved) pages."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.eventpro import EventProSite, add_details, listing_url, parse_listing

FIX = Path(__file__).parent / "fixtures" / "eventpro"
TZ = "Asia/Jerusalem"
HEFER = EventProSite(
    url="https://www.mk-hefer.org.il",
    did=4547,
    listing_id=13396,
    name="מרכז קהילתי עמק חפר",
    city="עמק חפר",
    localities=["כפר ויתקין", "גבעת חיים איחוד"],
)
DN = EventProSite(url="https://www.matnasdn.co.il", did=4451, name='מתנ"ס דרום נתניה', city="נתניה")
SKIP = ["קורס", "קתדרה", "דרוש", "חוגים"]


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_listing_cards_become_events_courses_dropped() -> None:
    events = parse_listing(_read("hefer_listing.html"), HEFER, TZ, SKIP)
    assert len(events) == 15  # 16 cards, the "קתדרה" course dropped
    show = next(e for e in events if e.source_event_id == "4547:46711")
    assert show.title.startswith("תעלול")
    assert show.start_at.date().isoformat() == "2026-11-05"
    assert show.min_price == Decimal(30)
    assert str(show.images[0]).startswith("https://www.mk-hefer.org.il/html5/web/4547/46711")
    assert str(show.url).endswith("_ID=46711&did=4547")


def test_home_page_listing_skips_job_ads() -> None:
    events = parse_listing(_read("dn_home.html"), DN, TZ, SKIP)
    titles = [e.title for e in events]
    assert any("Elton John" in t for t in titles)
    assert not any("דרוש" in t or "חוגים" in t for t in titles)
    sigd = next(e for e in events if e.source_event_id == "4451:46142")
    assert sigd.start_at.isoformat() == "2026-11-08T17:00:00+02:00" and sigd.time_known
    assert sigd.venue_name == "קפה לנוער קריית נורדאו" and sigd.city == "נתניה"


def test_event_page_gives_place_locality_and_description() -> None:
    (event,) = [
        e for e in parse_listing(_read("hefer_listing.html"), HEFER, TZ, []) if e.source_event_id == "4547:46444"
    ]
    event.title = "x"
    event = add_details(event, _read("hefer_show.html"), HEFER)
    assert event.venue_name == "בית העם, כפר ויתקין" and event.city == "כפר ויתקין"
    assert event.description
    assert not event.is_sold_out


def test_listing_url_pages_and_home() -> None:
    assert listing_url(HEFER, 17).endswith("sbs.taf?_id=13396&sm=&g=13396&did=4547&_startPRO4=17")
    assert listing_url(DN, 1) == "https://www.matnasdn.co.il/"
