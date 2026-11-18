"""Israel Music Festival parser test on the real wp-json list, two concert pages and their Wix registration pages'
JSON-LD (November 2026)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import israelmusicfest as imf

FIXTURES = Path(__file__).parent / "fixtures" / "israelmusicfest"
TZ = "Asia/Jerusalem"
CFG = imf.IsraelMusicFestSettings.model_validate(Settings().sources["israelmusicfest"])


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _posts() -> dict[str, dict]:
    return {p["id"]: p for p in map(imf.parse_post, json.loads(_read("posts.json"))) if p}


def test_posts_list() -> None:
    posts = _posts()
    dorot, opening, panel = posts["1620"], posts["1513"], posts["2026"]
    assert dorot["title"] == "תזמורת הג׳אז הישראלית מארחת את אבישי כהן"  # "| 1.12 | קיבוץ דורות" cut
    assert (dorot["day"], dorot["city"]) == (date(2026, 12, 1), "דורות")
    assert dorot["image"] and dorot["image"].startswith("https://israelmusicfest.co.il/wp-content/uploads/")
    # Published in 2025, reused for 2026's opening concert: the year comes from the last edit.
    assert opening["day"] == date(2026, 11, 27) and opening["title"].startswith("קונצרט פתיחה חגיגי | תזמורת")
    assert panel["city"] == "חיפה"  # its only category is "2.12 | חיפה"
    assert imf.category_city("מעלות | Ma'alot") == "מעלות" and imf.category_city("2.12") is None
    assert imf._title_day("27.11", "2024-11-01T10:00:00") == date(2024, 11, 27)  # a past year's post: a past day


def test_concert_from_page_and_registration() -> None:
    post, page = _posts()["1620"], _read("page_dorot.html")
    seller = imf.seller_link(page, CFG.seller_host)
    assert seller == "https://www.divasandgentlemen.com/event-details/hg-hmwsyqh-hysrlyt-2026-tzmwrt-hgz-hysrlyt-2"
    event = imf.build_event(post, page, seller, _read("wix_dorot.html"), CFG, TZ)
    assert event is not None
    assert event.start_at == datetime(2026, 12, 1, 20, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at is None  # the seller's "21:30 the next day"
    assert (event.venue_name, event.city, event.address) == ("אולם דורות שער הנגב", "דורות", None)  # "Dorot, Israel"
    assert event.extra["purchase_url"] == seller and str(event.url) == post["link"]
    assert [(t.price, t.is_free) for t in event.tickets] == [(0, True)] and event.min_price == 0
    assert not event.is_sold_out
    assert event.description and "אבישי כהן" in event.description
    genres = Enricher(Settings().enrichment, TZ).enrich(event)
    assert genres is not None and {"israeli", "festival"} <= set(
        genres.genres
    )  # declared only: the text is a programme


def test_hall_line_without_date_sold_out_and_no_registration_page() -> None:
    post, page = _posts()["2026"], _read("page_panel.html")
    assert imf.page_venue(page) == "אולם 207, המשכן לאמנויות, אוניברסיטת חיפה"  # "<hall>, 14:00"
    seller = imf.seller_link(page, CFG.seller_host)
    sold = imf.build_event(post, page, seller, _read("wix_soldout.html"), CFG, TZ)
    assert sold is not None and sold.is_sold_out
    # Registration page down: the page's own date and time, no price.
    bare = imf.build_event(post, page, seller, None, CFG, TZ)
    assert bare is not None and bare.start_at == datetime(2026, 12, 2, 14, 0, tzinfo=ZoneInfo(TZ))
    assert bare.end_at is None and bare.venue_name == "אולם 207, המשכן לאמנויות, אוניברסיטת חיפה"
    assert imf.build_event(post, "<html></html>", None, None, CFG, TZ) is None  # no date at all
