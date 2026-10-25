"""Airdrop: listing cards and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.airdrop import listing_urls, parse_event

TZ = "Asia/Jerusalem"
SITE = "https://airdrop.co.il"
FIXTURES = Path(__file__).parent / "fixtures/airdrop"


def _event(name: str, path: str):
    return parse_event((FIXTURES / name).read_text("utf-8"), f"{SITE}/{path}", TZ)


def test_listing_links_every_card_once() -> None:
    urls = listing_urls((FIXTURES / "events.html").read_text("utf-8"), SITE)
    assert len(urls) >= 40 and len(urls) == len(set(urls))
    assert urls[0] == f"{SITE}/justloveit-fullmoon"
    assert f"{SITE}/shalvata/spoons-last-dance" in urls


def test_club_night_with_price_range_and_seller_link() -> None:
    e = _event("shalvata_spoons-last-dance.html", "shalvata/spoons-last-dance")
    assert e is not None and e.source_event_id == "shalvata/spoons-last-dance"
    assert e.title == "Spoons בשלוותה · סגירת עונת הקיץ"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-26T22:00:00+02:00"
    assert e.venue_name == "שלוותה תל אביב" and e.city == "תל אביב" and e.country is None
    assert (e.min_price, e.max_price, e.currency) == (Decimal(110), Decimal(125), "ILS")
    assert e.artists == ["DARCO", "ADAM TEN"] and e.artist_profiles[0].image_url
    assert e.source_genres == ["טכנו ואלקטרוני"]
    assert e.extra["purchase_url"] == "https://www.eventer.co.il/event/dyk2f/LxL9L"


def test_referral_dropped_and_end_before_start_rolls_over() -> None:
    e = _event("justloveit-fullmoon.html", "justloveit-fullmoon")
    assert e is not None and e.min_price == Decimal("134.1")
    assert e.extra["purchase_url"] == "https://go-out.co/event/1791731808221"
    assert e.end_at is not None and e.start_at is not None and e.end_at > e.start_at


def test_trip_abroad_gets_its_country() -> None:
    e = _event("anyma-athens.html", "anyma-athens")
    assert e is not None and e.country == "GR" and e.city == "אתונה" and e.venue_name == "OAKA"
    assert e.extra["purchase_url"] == "https://takebreaktravel.com/events/anyma-athens-31-10"
    # The page stamps Israel's +02:00 on Athens' wall time; read it in Athens' zone.
    assert e.start_at is not None and e.start_at.hour == 16 and str(e.start_at.tzinfo) == "Europe/Athens"
