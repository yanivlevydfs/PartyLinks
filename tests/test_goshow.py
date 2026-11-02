"""GoShow: home-page cards and a date's page, on trimmed real pages (no network)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.goshow import GoShowSettings, add_page, card_event, parse_listing

FIXTURES = Path(__file__).parent / "fixtures" / "goshow"
TZ = "Asia/Jerusalem"
SETTINGS = GoShowSettings(cron="21 7,19 * * *")


def _cards() -> list[dict]:
    return parse_listing((FIXTURES / "home.html").read_text(encoding="utf-8"), TZ)


def test_listing_reads_each_date_once() -> None:
    cards = _cards()
    assert [c["date_id"] for c in cards] == ["55398", "57539"]  # the repeated card is read once
    first = cards[0]
    assert (first["show_id"], first["title"]) == ("14087", "נופר סויסה במופע סטנדאפ")
    assert (first["venue"], first["city"]) == ("בית ציוני אמריקה", "תל אביב-יפו")
    assert first["start"].isoformat() == "2026-09-18T21:00:00+03:00"
    # A venue with its street: "בר גיורא, אבן גבירול 30 , תל אביב-יפו" -> the city is the last part.
    assert cards[1]["city"] == "תל אביב-יפו" and cards[1]["venue"].startswith("בר גיורא")


def test_date_page_gives_title_artwork_prices_and_producer() -> None:
    event = card_event(_cards()[0], SETTINGS)
    assert str(event.url) == "https://www.goshow.co.il/show/14087/55398/"
    assert event.address == "בית ציוני אמריקה, תל אביב-יפו"
    add_page(event, (FIXTURES / "show.html").read_text(encoding="utf-8"), SETTINGS.site_url)
    assert event.title == "נופר סויסה במופע סטנדאפ"
    assert event.images == ["https://www.goshow.co.il/manager/userfiles/gallery/גלריה517.png"]
    assert event.description and "סטנדאפיסטית" in event.description
    # The open tier only: the student and soldier rows are commented out in the page.
    assert [(t.name, t.price) for t in event.tickets] == [("רגיל", Decimal(73))]
    assert (event.min_price, event.max_price) == (Decimal(73), Decimal(73))
    assert event.extra["producer"] == "קומדי בר"
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres


def test_heading_wins_over_an_unescaped_og_title() -> None:
    """og:title of 'ד"ר שי בידרמן' reads as 'ד': the page's heading has the full title."""
    event = card_event(_cards()[1], SETTINGS)
    page = '<meta property="og:title" content="ד"ר שי"><div class="showDescTitle"><h3>ד"ר שי בידרמן</h3></div>'
    add_page(event, page, SETTINGS.site_url)
    assert event.title == 'ד"ר שי בידרמן'
