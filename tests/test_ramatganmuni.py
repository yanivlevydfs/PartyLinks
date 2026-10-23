"""Ramat Gan municipality: event-lobby cards and event details to ScrapedEvent."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.ramatganmuni import parse_event, parse_listing

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/ramatganmuni"
SITE, MEDIA = "https://www.ramat-gan.muni.il", "https://cms-media.ramat-gan.muni.il"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def _cards() -> dict[str, dict]:
    return {c["detailsLink"]["url"]: c for c in parse_listing(_load("event_lobby.json"))}


def test_listing_each_event_once_without_box_office_mirrors() -> None:
    assert list(_cards()) == ["/events/shabbat-hazanut-and-piyyut/", "/events/storms-of-yemen/"]


def test_paid_event_sold_elsewhere() -> None:
    e = parse_event(_load("event_yemen.json"), _cards()["/events/storms-of-yemen/"], SITE, MEDIA, "רמת גן", TZ)
    assert e.source_event_id == "storms-of-yemen" and str(e.url) == SITE + "/events/storms-of-yemen/"
    assert e.start_at.isoformat() == "2026-11-11T20:30:00+02:00"
    assert (e.venue_name, e.address, e.city) == ("תיאטרון ראסל", "רחוב מנדל מתיתיהו 11", "רמת גן")
    assert (e.min_price, e.max_price) == (Decimal(19), Decimal(49))
    assert [t.name for t in e.tickets] == ["לתושב רמת גן", "תושב חוץ"]
    assert e.extra["purchase_url"].startswith("https://tickchak.co.il/")
    assert e.image_url.startswith(MEDIA + "/media/") and "%D7" in e.image_url and e.description


def test_free_entry_without_prices() -> None:
    card = _cards()["/events/shabbat-hazanut-and-piyyut/"]
    e = parse_event(_load("event_shabbat.json"), card, SITE, MEDIA, "רמת גן", TZ)
    assert e.start_at.isoformat() == "2026-10-23T00:00:00+03:00"
    assert e.min_price == 0 and e.tickets[0].is_free and e.extra == {}
    assert e.source_genres == ["אירועי הקהל הדתי"] and e.organizer == "עיריית רמת גן"
