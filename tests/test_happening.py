"""StayHappening / HappeningNext (allevents.in mirrors): a town page's JSON-LD and the two sites' event
pages, on trimmed real answers (26.11.2026)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.happening import (
    HappeningNextScraper,
    HappeningSettings,
    StayHappeningScraper,
    country_of,
    listing_rows,
    page_tags,
    parse_event_page,
    seller_link,
    slug,
)

FIXTURES = Path(__file__).parent / "fixtures" / "happening"
TZ = "Asia/Jerusalem"
SH = HappeningSettings(cron="11 3,15 * * *")
HN = HappeningSettings(
    cron="13 4,16 * * *",
    site_url="https://happeningnext.com",
    category_path="/{city}/{category}",
    event_marker="/event/",
    city_page_cap=0,
)
SH_URL = "https://stayhappening.com/e/shlomo-artzi-in-tel-aviv-yafo-E16UG23294MC"
HN_URL = "https://happeningnext.com/event/shlomo-artzi-in-tel-aviv-yafo-eid1kbvqll4p15"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _row(url: str) -> dict[str, Any]:
    return {"url": url, "name": "Shlomo Artzi in Tel Aviv-yafo", "startDate": "2026-11-27"}


def test_stayhappening_event_page() -> None:
    event = parse_event_page(
        _page("sh_event.html"), _row(SH_URL), {"music"}, SH, "stayhappening", TZ, StayHappeningScraper.tag_pattern
    )
    assert event is not None
    assert event.source_event_id == "E16UG23294MC"
    assert event.title == "Shlomo Artzi in Tel Aviv-yafo"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-27T13:00:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-11-27T15:00:00+02:00"
    assert event.time_known
    assert event.venue_name == "היכל התרבות" and event.city == "Tel Aviv"
    assert event.latitude is not None and event.longitude is not None
    # The "Find tickets" button (base64), not the app store link beside it.
    assert event.extra["purchase_url"] == "https://www.bandsintown.com/e/108873907?came_from=281"
    assert {"music", "concerts", "live music", "pop"} <= set(event.source_genres)
    assert event.organizer == "Sound Curator"
    assert event.images and event.images[0].startswith("https://cdn.stayhappening.com/")


def test_happeningnext_event_page_has_no_seller_link() -> None:
    event = parse_event_page(
        _page("hn_event.html"), _row(HN_URL), set(), HN, "happeningnext", TZ, HappeningNextScraper.tag_pattern
    )
    assert event is not None
    assert event.source_event_id == "eid1kbvqll4p15"
    assert event.extra["purchase_url"] == HN_URL  # its button opens the allevents app
    assert {"music", "live music"} <= set(event.source_genres)
    footer = "<a href='https://happeningnext.com/p/cities'>x</a><a href='https://happeningnext.com/p/privacy'>x</a>"
    assert page_tags(footer, HN, HappeningNextScraper.tag_pattern) == set()


def test_seller_link_skips_app_stores() -> None:
    assert seller_link('<button data-link="https://play.google.com/store/apps/details?id=x">', SH) is None
    assert seller_link('<button data-link="not base64 at all!">', SH) is None


def test_city_page_rows_and_category_links() -> None:
    html = _page("sh_city.html")
    rows = listing_rows(html, SH)
    assert len(rows) == 12 and rows[0]["url"] == SH_URL
    assert rows[0]["startDate"] == "2026-11-27"  # the listing has the day only: the page gives the time
    tags = page_tags(html, SH, StayHappeningScraper.tag_pattern)
    assert {"concerts", "live-music", "candlelight-concerts"} <= tags
    assert not any("shlomo" in t for t in tags)  # event links are no category


def test_country_and_town_slug() -> None:
    assert country_of({"location": {"address": {"addressCountry": "IL"}}}) == "IL"
    assert country_of({}) is None
    assert slug("Tel Aviv") == "tel+aviv"
    assert slug(" Beit  Shemesh ") == "beit+shemesh"


def test_enrichment_gives_site_genres() -> None:
    event = parse_event_page(
        _page("sh_event.html"), _row(SH_URL), {"music"}, SH, "stayhappening", TZ, StayHappeningScraper.tag_pattern
    )
    assert event is not None
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres
