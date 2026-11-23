from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.shimur import (
    Card,
    Page,
    ShimurSettings,
    build_event,
    is_skipped,
    parse_listing,
    parse_page,
    site_city,
    site_point,
    site_town,
)

FIXTURES = Path(__file__).parent / "fixtures" / "shimur"
TZ = "Asia/Jerusalem"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _day(value: datetime | None) -> date | None:
    return value.date() if value is not None else None


BASE = "https://shimur.org"


def _settings() -> ShimurSettings:
    return ShimurSettings(cron="38 6,18 * * *")


def _cards() -> dict[str, Card]:
    html = (FIXTURES / "events.html").read_text(encoding="utf-8")
    return {c.key: c for c in parse_listing(html, BASE)}


def _page(name: str, card_key: str) -> tuple[Card, Page]:
    card = _cards()[card_key]
    return card, parse_page((FIXTURES / name).read_text(encoding="utf-8"), card.url)


def test_listing_and_what_is_left_out() -> None:
    s = _settings()
    cards = _cards()
    assert cards["75597"].title == "שישי מורשת במוזיאון בית המאירי בצפת"
    assert cards["75597"].first == cards["75597"].last == date(2026, 11, 27)
    assert cards["75597"].categories == ["שישי מורשת"]
    assert cards["7632363"].categories == ['אירוע אתר הפרדסנות ע"ש מינקוב ברחובות']
    assert not is_skipped(cards["75597"], s)
    assert not is_skipped(cards["75699"], s)  # Hanukkah 1-10.12: one event over its days
    assert is_skipped(cards["265458"], s)  # "הזמנת סיור": bookable on request, months long
    assert is_skipped(cards["zumoreshet"], s)  # online
    assert is_skipped(cards["75539"], s)  # November at a museum: a month's digest


def test_heritage_friday_at_a_site_with_its_point() -> None:
    s = _settings()
    card, page = _page("hameiri.html", "75597")
    assert page.site_name == "מוזיאון בית המאירי בצפת"
    assert page.site_url and "/sites/" in page.site_url
    point = site_point((FIXTURES / "site_hameiri.html").read_text(encoding="utf-8"))
    assert point == (32.9677877, 35.49159959999997)
    event = build_event(card, page, point, s, TZ)
    assert event is not None
    assert _iso(event.start_at) == "2026-11-27T10:00:00+02:00"  # "סיורים מודרכים בשעות 10:00 | 12:00"
    assert event.city == "צפת"
    assert (event.venue_name, event.latitude) == ("מוזיאון בית המאירי בצפת", 32.9677877)
    assert event.source_genres == ["שישי מורשת"]
    assert event.images and event.images[0].startswith("https://shimur.org/wp-content/uploads/")
    assert event.extra["purchase_url"] == card.url
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres


def test_study_day_with_hours_and_price() -> None:
    s = _settings()
    card, page = _page("kinneret.html", "76098")
    event = build_event(card, page, (32.7149, 35.5626), s, TZ)
    assert event is not None
    assert _iso(event.start_at) == "2026-11-27T09:00:00+02:00"
    assert event.end_at is not None and _iso(event.end_at) == "2026-11-27T12:30:00+02:00"
    assert event.min_price == Decimal(75)
    assert event.venue_name == "חצר כנרת"


def test_multi_day_programme_and_town_from_name() -> None:
    s = _settings()
    card, page = _page("oldyishuv.html", "75699")
    event = build_event(card, page, (None, None), s, TZ)  # no point: the name's town, geocoded by name
    assert event is not None
    assert event.city == "ירושלים"
    assert event.address == "מוזיאון חצר היישוב הישן, ירושלים"
    assert _day(event.start_at) == date(2026, 12, 1)
    assert event.end_at is not None and _day(event.end_at) == date(2026, 12, 10)


def test_site_town_and_no_site() -> None:
    assert site_town("המרכז למורשת הצ'רקסית - כפר כמא") == "כפר כמא"
    assert site_town("מוזיאון מכון איילון, רחובות") == "רחובות"
    assert site_town("חצר כנרת") is None
    card, page = _page("circassian.html", "76220")
    page.site_name = None
    assert build_event(card, page, (None, None), _settings(), TZ) is None


def test_site_city() -> None:
    s = ShimurSettings(cron="38 6,18 * * *", site_towns={"ליבלינג": "תל אביב-יפו"})
    assert (
        site_city("מוזיאון מכון איילון, רחובות", (31.916, 34.8103), s) == "רחובות"
    )  # the name, not the next town's pin
    assert site_city("מוזיאון בית המאירי בצפת", (None, None), s) == "צפת"
    assert site_city("בית ליבלינג – מרכז העיר הלבנה", (None, None), s) == "תל אביב-יפו"
