import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.motj import (
    CastiliaShow,
    MotjSettings,
    castilia_events,
    castilia_show_id,
    coming_post,
    parse_castilia_listing,
    parse_castilia_show,
    post_clock,
    post_events,
    ticket_link,
)

FIXTURES = Path(__file__).parent / "fixtures" / "motj"
TZ = "Asia/Jerusalem"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _settings() -> MotjSettings:
    return MotjSettings.model_validate(get_settings().sources["motj"])


def _posts() -> dict[int, dict[str, Any]]:
    rows = json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def _show(show_id: str) -> CastiliaShow:
    s = _settings()
    show = CastiliaShow(show_id=show_id, url=f"https://castilia.co.il/he/Show/{show_id}/x")
    parse_castilia_show((FIXTURES / f"castilia_show_{show_id}.html").read_text(encoding="utf-8"), show, s)
    return show


def test_coming_posts_only() -> None:
    posts = _posts()
    today = date(2026, 11, 23)
    assert coming_post(posts[18914], today)  # 6.12.26
    assert not coming_post(posts[18903], today)  # 15.11.26: past
    assert not coming_post(posts[1363], today)  # an "archive" write-up
    assert not coming_post(posts[15399], today)  # a test post without a day


def test_castilia_listing_and_show() -> None:
    shows = parse_castilia_listing((FIXTURES / "castilia_listing.html").read_text(encoding="utf-8"), _settings())
    ids = [s.show_id for s in shows]
    assert ids == ["343", "387", "388", "394", "373", "395", "397", "398"]
    assert shows[1].title == "העבריות"
    assert shows[1].url and shows[1].url.startswith("https://castilia.co.il/he/Show/387/")
    orka = _show("373")
    assert [(p.event_id, p.day, p.clock) for p in orka.performances] == [
        ("5706", date(2026, 12, 24), time(14, 0)),
        ("5707", date(2026, 12, 24), time(21, 0)),
    ]
    assert orka.image and orka.image.startswith("https://castilia.co.il/cache/")
    assert " " not in orka.image  # the file name's spaces are quoted


def test_post_with_castilia_performance() -> None:
    s = _settings()
    post = _posts()[18914]
    assert castilia_show_id(post) == "387"
    [event] = post_events(post, _show("387"), s, TZ)
    assert event.title == "העבריות שרות אהוד מנור: מופע חדש במוזיאון הסובלנות ירושלים"
    assert _iso(event.start_at) == "2026-12-06T20:30:00+02:00"
    assert event.source_event_id == "18914-5807"
    assert event.extra["purchase_url"] == "https://castilia.smarticket.co.il/event/5807"  # the Smarticket tenant's page
    assert (event.venue_name, event.city, event.latitude, event.longitude) == (
        "מוזיאון הסובלנות ירושלים",
        "ירושלים",
        31.7791314,
        35.2200716,
    )
    assert event.images and event.images[0].startswith("https://motj.org.il/wp-content/uploads/")
    assert "הופעה חיה" in event.source_genres
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "live_show" in enriched.genres


def test_postponed_show_with_a_workshop() -> None:
    s = _settings()
    events = post_events(_posts()[18443], _show("373"), s, TZ)
    assert [(e.title, _iso(e.start_at)) for e in events] == [
        ("סדנת הקול הפנימי עם אורקה", "2026-12-24T14:00:00+02:00"),
        ("מעגלים – מפגש מוזיקלי חי במדיות שונות: אורקה מארחת את נדב דגון", "2026-12-24T21:00:00+02:00"),
    ]  # "האירוע נדחה ל-24.12 |" dropped from the title
    assert events[0].source_genres == ["סדנה"]
    assert events[1].extra["purchase_url"] == "https://castilia.smarticket.co.il/event/5707"
    prices = {e.min_price for e in events}
    assert prices and None not in prices  # "הופעה: 99 ש"ח" in the post's good-to-know text


def test_post_without_castilia_page_uses_its_lines() -> None:
    s = _settings()
    [event] = post_events(_posts()[19030], None, s, TZ)  # Castilia's page not read: the post's own day and clock
    assert _iso(event.start_at) == "2027-01-28T20:30:00+02:00"  # the start, not the doors at 19:30
    assert event.extra["purchase_url"].startswith("https://castilia.co.il/he/Show/397/")
    [ranged] = post_events(_posts()[18903], None, s, TZ)
    assert (_iso(ranged.start_at), _iso(ranged.end_at)) == ("2026-11-15T18:30:00+02:00", "2026-11-15T19:30:00+02:00")


def test_two_day_post_is_two_events() -> None:
    events = post_events(_posts()[18769], None, _settings(), TZ)
    assert [e.start_at.date() for e in events if e.start_at] == [date(2026, 10, 13), date(2026, 10, 14)]
    assert len({e.source_event_id for e in events}) == 2


def test_castilia_only_show() -> None:
    show = _show("387")
    show.title = "העבריות"
    [event] = castilia_events(show, _settings(), TZ)
    assert event.source_event_id == "castilia-5807"
    assert str(event.url) == show.url and event.description and "אהוד מנור" in event.description


def test_ticket_links() -> None:
    s = _settings()
    assert ticket_link("https://tickets.motj.org.il/event/2538", s) == "https://motj.smarticket.co.il/event/2538"
    assert ticket_link("https://www.youtube.com/watch?v=x", s) is None
    assert ticket_link("https://www.eventer.co.il/abc", s) == "https://www.eventer.co.il/abc"
    assert post_clock("פתיחת דלתות: 19:30 | תחילת ההרצאה: 20:00") == (time(20, 0), None)
    assert post_clock("התכנסות בשעה 21:30") == (time(21, 30), None)
