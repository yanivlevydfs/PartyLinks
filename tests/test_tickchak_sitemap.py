import json
from pathlib import Path

import pytest

from partylinks.config import EnrichmentSettings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.tickchak import parse_event
from partylinks.scrapers.tickchak_sitemap import event_pages, page_item

FIXTURE = Path(__file__).parent / "fixtures" / "tickchak_sitemap" / "event_ePLdPWRaL.html"
SITE = "https://live.tickchak.co.il"


def test_event_pages_keep_event_urls_only() -> None:
    xml = (
        "<urlset><url><loc>https://live.tickchak.co.il/shows/event/eKnMMrean</loc></url>"
        "<url><loc> https://live.tickchak.co.il/event/erLrwEwYx </loc></url>"
        "<url><loc>https://live.tickchak.co.il/tel-aviv-menorah-hall</loc></url>"
        "<url><loc>https://live.tickchak.co.il/event/erLrwEwYx</loc></url></urlset>"
    )
    assert event_pages(xml) == {
        "eKnMMrean": "https://live.tickchak.co.il/shows/event/eKnMMrean",
        "erLrwEwYx": "https://live.tickchak.co.il/event/erLrwEwYx",
    }


def test_page_item_parses_like_a_slider_item() -> None:
    url = SITE + "/event/ePLdPWRaL"
    event = parse_event(page_item(FIXTURE.read_text(encoding="utf-8"), url), SITE)
    assert event.source_event_id == "ePLdPWRaL"
    assert str(event.url) == url
    assert event.title == "כוכבים לנצח"
    assert event.start_at.isoformat() == "2026-11-01T18:30:00+00:00"
    assert event.venue_name == "היכל התרבות קריית אתא"
    assert event.city == "קרית אתא"
    # The seller's page (pageUrl) is the purchase link when the item has no link.
    assert event.extra["purchase_url"].startswith("https://tickets.friends-hist.co.il/")
    assert str(event.min_price) == "59.00"
    assert Enricher(EnrichmentSettings(), "Asia/Jerusalem").enrich(event) is not None


def test_page_without_an_event_is_a_parse_error() -> None:
    html = '<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"initialState": {"event": {}}}}}</script>'
    with pytest.raises(ParseError):
        page_item(html, SITE + "/event/x")


def test_category_slug_from_the_pages_main_categories() -> None:
    state = {
        "event": {"entityId": "eAjDXDXoj", "date": 1793556000, "mainCategory": 10, "link": "https://seller/1"},
        "page": {"mainCategories": [{"id": 1, "friendlyUrl": "shows"}, {"id": 10, "friendlyUrl": "theater"}]},
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"initialState": state}}})}</script>'
    item = page_item(html, SITE + "/theater/event/eAjDXDXoj")
    assert item["categorySlug"] == "theater"
    assert item["link"] == "https://seller/1"
    assert item["url"] == "/theater/event/eAjDXDXoj"
    state["event"]["mainCategory"] = 28  # a number the page does not list: no category
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"initialState": state}}})}</script>'
    assert page_item(html, SITE + "/event/eAjDXDXoj")["categorySlug"] is None
