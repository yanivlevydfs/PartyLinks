"""GETIN: filter-events rows and event pages to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal

from partylinks.scrapers.getin import GetinSettings, category_list, merge_rows, parse_row, with_page

TZ = "Asia/Jerusalem"
S = GetinSettings(cron="42 7,19 * * *")
CATS = {
    c["id"]: c["name"]
    for c in category_list({"data": [{"id": 9, "name": {"en": "Techno"}}, {"id": 1, "name": {"en": "Nightlife"}}]})
}

OLD = {
    "event_id": 425771, "title": "unKommon events Presents | Guy Gerber | Rumors", "start_date": "2026-12-02 20:00:00",
    "end_date": "2026-12-03 03:00:00", "time_zone": "America/New_York", "address": "300 NE 71st St, Miami, FL 33138, USA",
    "lat": "25.8403145", "lon": "-80.19", "min_price": "40.00", "max_price": "225.00", "country_short_name": "US",
    "city": "Miami", "categories": [1, 9], "file": "_cover_20260819_162449_425771.png", "producer_name": "Rumors",
    "artists": [{"name": "Guy Gerber", "image": "https://i.scdn.co/image/a", "genres": ["techno melódico"]}], "is_new_platform": False,
}  # fmt: skip
NEW = {
    "event_id": 41328, "title": "Roger Sanchez - Nov 20 inside FUTURE", "start_date": "2026-11-20 22:00:00",
    "end_date": "2026-11-21 04:30:00", "time_zone": "America/Toronto", "country_short_name": "CA", "city": "Toronto",
    "min_price": 35, "max_price": 35, "categories": [], "artists": [], "is_new_platform": True,
    "new_platform_url": "https://getin.xyz/event/41328?seller_code=getin",
    "new_platform_images": {"desktop": "https://static.getin.xyz/gallery/desktop_cover_41328.png"},
    "venue": {"name": "Future Nightlife", "address": "570 Queen St W, Toronto", "lat": "43.64", "lon": "-79.40"},
}  # fmt: skip
ISRAEL = {"event_id": 7, "title": "מסיבת גג", "start_date": "2026-12-10 23:00:00", "end_date": "", "country_short_name": "IL",
          "city": "הרצליה", "address": "מרטין בובר 6, הרצליה, ישראל", "lat": "32.16", "lon": "34.84", "categories": [9]}  # fmt: skip


def test_rows_merged_once_per_id() -> None:
    rows = merge_rows([], {"events": [OLD, NEW]})
    assert [r["event_id"] for r in merge_rows(rows, {"events": [NEW, ISRAEL]})] == [425771, 41328, 7]


def test_old_platform_event_abroad_in_its_zone_priced_once_the_page_names_the_currency() -> None:
    e = parse_row(OLD, CATS, S, TZ)
    assert e is not None and str(e.url) == "https://get-in.com/en/425771" and e.country == "US"
    assert e.time_zone == "America/New_York" and e.start_at.isoformat() == "2026-12-02T20:00:00-05:00"
    assert e.images == ["https://static.get-in.com/gallery/tablet_cover_20260819_162449_425771.png"]
    assert e.source_genres == ["nightlife", "techno", "techno melódico"] and e.artists == ["Guy Gerber"]
    assert e.min_price is None  # no currency yet: never priced as shekels
    page = '<script id="ng-state" type="application/json">{"event_details": {"currency": "USD"}}</script>'
    priced = with_page(e, page)
    assert priced.currency == "USD" and priced.min_price == Decimal(40) and priced.max_price == Decimal(225)
    assert "price_range" not in priced.extra


def test_new_platform_page_without_seller_code_and_default_genre() -> None:
    e = parse_row(NEW, CATS, S, TZ)
    assert e is not None and str(e.url) == "https://getin.xyz/event/41328"
    assert e.venue_name == "Future Nightlife" and (e.latitude, e.longitude) == (43.64, -79.40)
    assert e.images == ["https://static.getin.xyz/gallery/desktop_cover_41328.png"] and e.source_genres == ["nightlife"]


def test_israeli_night_has_no_country_and_no_placeholder_end() -> None:
    e = parse_row(ISRAEL, CATS, S, TZ)
    assert e is not None and e.country is None and e.time_zone is None and e.end_at is None
    assert e.start_at.isoformat() == "2026-12-10T23:00:00+02:00" and e.city == "הרצליה"
