"""Municipal events CMS (Rosh HaAyin, Kfar Saba): listing cards and event pages to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.munievents import Card, MuniSite, build_event, parse_listing

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/munievents"
ROSH = MuniSite(url="https://www.rosh-haayin.muni.il", city="ראש העין")
KFAR = MuniSite(url="https://www.kfar-saba.muni.il", city="כפר סבא")


def _read(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def _cards() -> dict[str, Card]:
    return {c.event_id: c for c in parse_listing(_read("rosh_haayin_events.html"), ROSH.url)}


def test_listing_keeps_own_cards_only() -> None:
    cards = _cards()
    assert list(cards) == ["1827", "1844", "1875", "1830", "1876"]  # the two Smarticket cards are left out


def test_sold_out_paid_event() -> None:
    e = build_event(_cards()["1830"], _read("rosh_haayin_1830.html"), ROSH, TZ)
    assert e is not None and e.source_event_id == "rosh-haayin:1830" and e.title == "סדנת תכשיטנות"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-01T19:30:00+02:00" and e.end_at is None
    assert (e.venue_name, e.address, e.city) == ("מרכז פיס תורני", "רח' המתמיד 23 פינת יהודה הלוי", "ראש העין")
    assert e.min_price == Decimal(40) and e.is_sold_out and e.tickets[0].status is TierStatus.SOLD_OUT
    assert e.source_genres == ["נשים", "סדנה"] and e.description and e.description.startswith("בואי להתפנק")
    assert e.image_url == "https://www.rosh-haayin.muni.il/uploads/n/1791380822.2495.jpg"  # full size, not c-480


def test_on_sale_with_remaining_and_end_time() -> None:
    e = build_event(_cards()["1875"], _read("rosh_haayin_1875.html"), ROSH, TZ)
    assert e is not None and e.end_at is not None and e.end_at.isoformat() == "2026-10-25T20:30:00+02:00"
    assert e.tickets[0].price == Decimal(10) and e.tickets[0].remaining == 5 and not e.is_sold_out


def test_no_tickets_row_season_and_undated() -> None:
    cards = _cards()
    e = build_event(cards["1876"], None, ROSH, TZ)
    assert e is not None and e.tickets == [] and e.venue_name == 'מתנ"ס גבעת הסלעים' and e.address is None
    assert "c-480" in (e.image_url or "")  # no page: the card's thumbnail
    season = build_event(cards["1844"], None, ROSH, TZ)
    assert season is not None and season.start_at is not None
    assert season.start_at.isoformat() == "2026-09-02T18:00:00+03:00"  # its first session
    assert build_event(cards["1827"], None, ROSH, TZ) is None  # "ללא תאריך"


def test_kfar_saba_same_engine() -> None:
    card = parse_listing(_read("kfar_saba_events.html"), KFAR.url)[0]
    e = build_event(card, _read("kfar_saba_detail.html"), KFAR, TZ)
    assert e is not None and e.source_event_id == "kfar-saba:6923" and e.city == "כפר סבא"
    assert (e.venue_name, e.address) == ("ארט קיימא", 'רחוב אז"ר 45') and e.tickets == []
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-23T09:30:00+03:00"
    assert e.description and "פוטותרפיה" in e.description
