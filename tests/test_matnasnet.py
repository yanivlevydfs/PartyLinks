"""Matnasnet community-centre CMS: listing links and event pages (several themes) to ScrapedEvent."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.matnasnet import (
    MatnasnetSite,
    build_event,
    covered_smarticket_hosts,
    listing_urls,
    parse_listing,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/matnasnet"
SKIP = frozenset({"phk.smarticket.co.il"})
HOF = MatnasnetSite(url="https://www.hof-hasharon.atarix.co.il", city="חוף השרון", localities=["געש", "יקום"])


def _event(name: str, site: MatnasnetSite) -> ScrapedEvent | None:
    event_id = name.split("_")[1].removesuffix(".html")
    url = f"{site.url}/page.php?type=event&id={event_id}"
    return build_event(url, (FIXTURES / name).read_text("utf-8"), site, TZ, SKIP)


def test_listing_leaves_out_the_archive_and_month_pages() -> None:
    assert parse_listing((FIXTURES / "givatzeev_events.html").read_text("utf-8")) == ["13593"]
    site = MatnasnetSite(url="https://www.elkana.org.il", city="אלקנה", months=2)
    assert listing_urls(site, date(2026, 12, 5))[1:] == [
        "https://www.elkana.org.il/page.php?type=events&m=1&y=2027",
        "https://www.elkana.org.il/page.php?type=events&m=2&y=2027",
    ]


def test_card_theme_with_tickchak_link() -> None:
    e = _event("givatzeev_13593.html", MatnasnetSite(url="https://www.givatzeev.org.il", city="גבעת זאב"))
    assert e is not None and e.source_event_id == "givatzeev.org.il:13593" and e.title == "ילדים בהיכל - מה קרה בארץ מי"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-27T17:30:00+02:00"
    assert e.end_at is None  # the calendar link's end is a default, not the site's
    assert (e.venue_name, e.address, e.city) == ("היכל התרבות", "גבעת זאב", "גבעת זאב")
    assert e.min_price == Decimal(45) and e.extra == {"purchase_url": "https://tickchak.co.il/99286"}
    assert e.organizer == "מתנס גבעת זאב" and e.image_url and "matnasnet-static-files" in e.image_url


def test_tags_and_regional_locality() -> None:
    e = _event("hofhasharon_9042.html", HOF)
    assert e is not None and e.source_genres == ["ילדים"]
    assert (e.venue_name, e.city) == ("מועדון געש", "געש")  # a kibbutz of the council
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-25T17:00:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-25T18:00:00+02:00"


def test_hours_line_theme() -> None:
    e = _event("matnasny_63319.html", MatnasnetSite(url="https://www.matnasny.org.il", city="חיפה"))
    assert e is not None and e.venue_name == 'מתנ"ס נווה יוסף' and e.min_price == Decimal(10)
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-25T11:00:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-25T12:30:00+02:00"


def test_labelled_theme_keeps_uncovered_smarticket() -> None:
    e = _event("sdotnegev_53199.html", MatnasnetSite(url="https://www.m-sdotnegev.org.il", city="שדות נגב"))
    assert e is not None and e.venue_name == "ספריית שדות נגב" and e.city == "שדות נגב"
    assert e.extra["purchase_url"] == "https://sdotnegev.smarticket.co.il/event/1264"  # not a covered host
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-17T17:00:00+02:00"


def test_skips_covered_smarticket_undated_and_logo_image() -> None:
    assert (
        _event("mymatnas_12570.html", MatnasnetSite(url="https://www.mymatnas.org.il", city="פרדס חנה-כרכור")) is None
    )
    assert _event("mako_89474.html", MatnasnetSite(url="https://www.mako.org.il", city="עכו")) is None  # "אין תאריך"
    e = _event("ganyavne_100030.html", MatnasnetSite(url="https://www.matnasganyavne.org", city="גן יבנה"))
    assert e is not None and e.image_url is None and e.venue_name == "ירושלים"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-25T00:00:00+03:00"  # no hour given


def test_covered_hosts_come_from_the_smarticket_block() -> None:
    hosts = covered_smarticket_hosts()
    assert "phk.smarticket.co.il" in hosts and "tickets.raanana.muni.il" in hosts
