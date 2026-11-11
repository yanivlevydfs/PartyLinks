"""Daniel Tel Aviv (gototlv.co.il): rows of its events.json."""

from __future__ import annotations

from decimal import Decimal

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.gototlv import GotoTlvSettings, artwork, parse_event

TZ = "Asia/Jerusalem"
S = GotoTlvSettings(cron="50 9,21 * * *")

PARTY = {
    "id": "decision-gagarin-1211", "title": "DECISION: אסטרל פרוג׳קשן וטלמסקה", "date": "2026-11-12",
    "endDate": "2026-11-13", "time": "23:00 עד 06:00 למחרת", "location": "גגרין, דרך קיבוץ גלויות 13",
    "category": "מסיבות", "price": 108, "priceText": "החל ב-108 ₪ כולל עמלה",
    "description": "ליין טראנס.", "url": "https://go-out.co/event/1793464308719", "published": True,
    "expiresAt": "2026-11-13T06:00:00+02:00", "overnight": True,
}  # fmt: skip
NO_CLOCK = {
    "id": "art-weekend", "title": "אוהבים אמנות. עושים אמנות.", "date": "2026-11-11", "endDate": "2026-11-14",
    "time": "שעות משתנות לפי מתחם", "location": "גן הכובשים, גלריות וסטודיו ברחבי העיר", "category": "תרבות",
    "price": 0, "priceText": "אירועי הליבה בחינם", "url": "https://www.tel-aviv.gov.il/Pages/MainItemPage.aspx?ItemID=14590",
    "published": True, "expiresAt": "2026-11-14T20:00:00+02:00",
}  # fmt: skip
SOCIAL = {
    "id": "people-like-us-1411", "title": "People Like Us: רוקדים מול הים", "date": "2026-11-14",
    "time": "16:00-20:30", "location": "האוזן; פרטי הכניסה בכרטיס", "category": "מסיבות", "price": None,
    "priceText": "חינם עד חצות בהרשמה; 60 ₪ לכל הלילה", "url": "https://www.instagram.com/peoplelikeustlv/",
    "published": True,
}  # fmt: skip


def test_party_with_seller_link_and_overnight_end() -> None:
    e = parse_event(PARTY, S, TZ)
    assert e is not None and e.source == "gototlv" and e.source_event_id == "decision-gagarin-1211"
    assert e.start_at.isoformat() == "2026-11-12T23:00:00+02:00" and e.time_known
    assert e.end_at is not None and e.end_at.isoformat() == "2026-11-13T06:00:00+02:00"
    assert (e.venue_name, e.address, e.city) == ("גגרין", "דרך קיבוץ גלויות 13, תל אביב-יפו", "תל אביב-יפו")
    assert str(e.url) == e.extra["purchase_url"] == "https://go-out.co/event/1793464308719"
    assert e.min_price == Decimal(108) and e.source_genres == ["מסיבות"]
    settings = Settings()
    assert "nightclubs" in Enricher(settings.enrichment, settings.timezone).enrich(e).genres


def test_no_clock_is_free_and_cultural() -> None:
    e = parse_event(NO_CLOCK, S, TZ)
    assert e is not None and not e.time_known and e.start_at.isoformat() == "2026-11-11T00:00:00+02:00"
    assert e.tickets[0].is_free and e.min_price == 0
    settings = Settings()
    assert "culture" in Enricher(settings.enrichment, settings.timezone).enrich(e).genres


def test_social_link_dropped_and_venue_note_cut() -> None:
    e = parse_event(SOCIAL, S, TZ)
    assert (
        e is not None and e.extra["purchase_url"] is None and str(e.url) == "https://gototlv.co.il/#people-like-us-1411"
    )
    assert e.venue_name == "האוזן" and e.address == "האוזן, תל אביב-יפו"
    assert e.tickets[0].is_free  # "חינם עד חצות" with no number
    assert e.end_at is None


def test_unpublished_skipped_and_logo_not_artwork() -> None:
    assert parse_event({**PARTY, "published": False}, S, TZ) is None
    assert artwork('<meta property="og:image" content="https://x.co/img/site-logo.png">') is None
    assert artwork('<meta property="og:image" content="https://x.co/e/1.jpg">') == "https://x.co/e/1.jpg"


def test_photography_from_title_not_from_small_print() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    festival = {**NO_CLOCK, "id": "photo-israel", "title": "פסטיבל הצילום הבינלאומי"}
    e = parse_event(festival, S, TZ)
    assert e is not None and "photography" in enricher.enrich(e).genres
    talk = {**NO_CLOCK, "id": "talk", "title": "ערב שירה", "description": "האירוע יצולם. צילום: דנה לוי"}
    e = parse_event(talk, S, TZ)
    assert e is not None and "photography" not in enricher.enrich(e).genres
