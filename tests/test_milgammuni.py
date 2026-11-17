"""Metropolinet city multisite (Bet She'an): events grid and event pages to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.milgammuni import MilgamSite, parse_event, parse_listing, split_place

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/milgammuni"
SITE = MilgamSite(url="https://bet-shean.org.il", city="בית שאן")
URL = "https://bet-shean.org.il/events/x/"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_listing_cards_in_grid_order() -> None:
    cards = parse_listing(_page("listing.html"), SITE.url)
    assert len(cards) == 4
    url, image = cards[0]
    assert url.startswith("https://bet-shean.org.il/events/%d7%91%d7%95%d7%a7%d7%a8")
    assert image is not None and image.startswith("https://bet-shean.org.il/wp-content/uploads/sites/64/")


def test_paid_morning_show_with_registration_link() -> None:
    e = parse_event(_page("event_morning.html"), URL, None, SITE, TZ)
    assert e is not None
    assert e.source_event_id == "bet-shean.org.il:31587"
    assert e.title == "בוקר תרבות: מצחיק עד דמעות – סיפורו של שייקה אופיר"
    assert e.start_at.isoformat() == "2026-11-17T10:00:00+02:00" and e.time_known
    assert (e.venue_name, e.address, e.city) == ("בית יד לבנים", "רחוב דוד רמז 6", "בית שאן")
    assert e.min_price == Decimal(40)
    assert e.extra["purchase_url"] == "https://did.li/yLAmC"  # not the header's "פנייה למוקד"
    assert e.image_url and "/uploads/sites/64/2026/11/" in e.image_url
    assert e.organizer == "עיריית בית שאן" and e.description and "שייקה אופיר" in e.description
    enriched = Enricher(get_settings().enrichment, TZ).enrich(e)
    assert enriched is not None and enriched.genres


def test_free_bar_meetup_drops_the_sign_up_form() -> None:
    e = parse_event(_page("event_bar.html"), URL, None, SITE, TZ)
    assert e is not None
    assert e.start_at.isoformat() == "2026-10-14T20:00:00+03:00"
    assert (e.venue_name, e.address) == ("OPAA – הבר היווני של דלאל", "שדרות הארבעה 3")
    assert e.min_price == 0 and e.tickets[0].is_free
    assert e.extra == {}  # forms.gle is a sign-up form, not a seller


def test_bare_place_and_listing_artwork_fallback() -> None:
    e = parse_event(_page("event_park.html"), URL, "https://x.example/a.jpg", SITE, TZ)
    assert e is not None
    assert (e.venue_name, e.address) == ("פארק האקליפטוס", None)
    assert e.image_url and e.image_url.startswith("https://bet-shean.org.il/")
    assert e.extra == {}


def test_split_place() -> None:
    assert split_place("מרכז הנוער - רח' הרב לסרי 2") == ("מרכז הנוער", "רח' הרב לסרי 2")
    assert split_place(None) == (None, None)


def test_page_without_date_is_skipped() -> None:
    page = _page("event_park.html").replace("30/9/2026 17:00", "יעודכן")
    assert parse_event(page, URL, None, SITE, TZ) is None
