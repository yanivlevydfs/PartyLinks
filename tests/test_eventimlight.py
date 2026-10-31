"""Eventim Light: shop page parsing, the Queue-it cookie-test redirect, past / cancelled events and
Common Crawl discovery (saved pages, no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import build_scraper
from partylinks.scrapers.eventimlight import (
    EventimLightScraper,
    cookie_redirect,
    load_known,
    parse_shop,
    shops_in_index,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eventimlight"
SHOP = "de/6395bc712f27c4432c8bda66"
BEFORE = datetime(2026, 10, 31, tzinfo=UTC)


def _scraper() -> EventimLightScraper:
    scraper = build_scraper("eventimlight", Settings())
    assert isinstance(scraper, EventimLightScraper)
    return scraper


def _shop(**replace: str) -> str:
    page = (FIXTURES / "shop.html").read_text(encoding="utf-8")
    for old, new in replace.items():
        page = page.replace(old, new)
    return page


def test_shop_page_gives_its_upcoming_events_with_place_price_and_category() -> None:
    settings = _scraper().settings.model_copy(update={"categories": {"party": ["nightclubs"]}})
    [event] = parse_shop(_shop(), SHOP, settings, now=BEFORE)
    assert event.title == "Trinity Hamburg Party - The Next Chapter"
    assert str(event.url) == "https://www.eventim-light.com/de/a/6395bc712f27c4432c8bda66/e/69cd89f7bca63b14867bb35c"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-07T20:00:00+01:00"
    assert event.end_at is None  # Eventim Light repeats the start as the end
    assert (event.venue_name, event.city, event.country) == ("Edelfettwerk", "Hamburg", "DE")
    assert event.address == "Schnackenburgallee 202, 22525 Hamburg, DE"
    assert (event.latitude, event.longitude) == (53.59522, 9.9055)
    assert (event.currency, event.min_price, event.is_sold_out) == ("EUR", Decimal("22.73"), False)
    assert (
        event.image_url
        == "https://www.eventim-light.com/de/api/image/69cd8acabca63b14867bb429/facebook_event_cover/webp"
    )
    assert event.source_genres == ["party", "nightclubs"] and event.extra["category"] == "Party"
    assert event.organizer == "Handelsagentur Kuehl - Jörg Kühl"


def test_israeli_venue_is_israel_not_abroad() -> None:
    [event] = parse_shop(_shop(**{'"country":"DE"': '"country":"IL"'}), SHOP, _scraper().settings, now=BEFORE)
    assert event.country is None


def test_past_and_cancelled_events_are_skipped() -> None:
    settings = _scraper().settings
    assert parse_shop(_shop(), SHOP, settings, now=datetime(2026, 11, 8, tzinfo=UTC)) == []
    assert parse_shop(_shop(**{'"status":"PUBLISHED"': '"status":"CANCELLED"'}), SHOP, settings, now=BEFORE) == []
    cancelled = _shop(**{"https://schema.org/EventScheduled": "https://schema.org/EventCancelled"})
    assert parse_shop(cancelled, SHOP, settings, now=BEFORE) == []


def test_cookie_test_page_is_followed_once_on_its_own_host(tmp_path: Path) -> None:
    cookie_page = (FIXTURES / "cookie_test.html").read_text(encoding="utf-8")
    target = cookie_redirect(cookie_page)
    assert target is not None and target.startswith("/?c=eventimlight&e=shopde")
    assert cookie_redirect(_shop()) is None
    shop = _shop(**{"2026-11-0": "2099-11-0"})  # always upcoming
    queue = "https://eventimlight.queue-it.net"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.path}")
        url = str(request.url)
        if request.url.path == "/de/a/000000000000000000000000/":
            return httpx.Response(404, text="gone")
        if request.url.host == "www.eventim-light.com" and "queueittoken" not in url and "passed" not in url:
            return httpx.Response(302, headers={"location": f"{queue}/?c=eventimlight&e=shopde"})
        if request.url.host == "eventimlight.queue-it.net" and "cid=" not in url:
            return httpx.Response(200, text=cookie_page)
        if request.url.host == "eventimlight.queue-it.net":  # the cookie-test target, on queue-it's host
            return httpx.Response(
                302, headers={"location": f"https://www.eventim-light.com/{SHOP}/?queueittoken=passed"}
            )
        return httpx.Response(200, text=shop)

    scraper = _scraper()
    known = tmp_path / "shops.json"
    known.write_text(json.dumps({"shops": ["de/000000000000000000000000"], "collections": ["CC-MAIN-2026-43"]}))
    scraper.settings = scraper.settings.model_copy(
        update={"known_file": known, "cdx_collections": 0, "seed_shops": [SHOP]}
    )
    scraper.http.request_delay_seconds = 0

    async def go() -> list:  # type: ignore[type-arg]
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert [e.source_event_id for e in events] == ["69cd89f7bca63b14867bb35c"]
    assert "eventimlight.queue-it.net/" in seen and not scraper.failures
    # The closed shop (404) leaves the known set; the crawls already read stay read.
    assert load_known(known) == {"shops": [], "collections": ["CC-MAIN-2026-43"]}


def test_common_crawl_lines_give_shops_per_market() -> None:
    lines = [
        json.dumps({"url": "https://www.eventim-light.com/de/a/6395bc712f27c4432c8bda66/e/69cd89f7bca63b14867bb35c"}),
        json.dumps({"url": "https://www.eventim-light.com/de/a/6395bc712f27c4432c8bda66/"}),
        json.dumps({"url": "https://www.eventim-light.com/uk/a/5f1e0c2a9b3d4e0011a2b3c4?lang=en"}),
        json.dumps({"url": "https://www.eventim-light.com/ch/a/5f1e0c2a9b3d4e0011a2b3c4"}),
        json.dumps({"url": "https://www.eventim-light.com/xx/a/5f1e0c2a9b3d4e0011a2b3c5/"}),  # not a market
        json.dumps({"url": "https://www.eventim-light.com/de/api/image/69cd8acabca63b14867bb429/x"}),
        json.dumps({"url": "https://www.eventim-light.com/de/a/6395bc712f27c4432c8bda66ff/"}),  # not an id
        "not json",
    ]
    assert shops_in_index(lines, "eventim-light.com", ["de", "uk", "ch"]) == {
        "de/6395bc712f27c4432c8bda66", "uk/5f1e0c2a9b3d4e0011a2b3c4", "ch/5f1e0c2a9b3d4e0011a2b3c4",
    }  # fmt: skip


def test_shop_categories_reach_the_site_genres() -> None:
    settings = _scraper().settings  # the configured category map
    enricher = Enricher(Settings().enrichment, "Asia/Jerusalem")
    expected = {"Party": "nightclubs", "Comedy & Kabarett": "stand_up", "Theater": "theatre", "Klassik": "classical_opera",
                "Kinderveranstaltung": "kids", "Lesung": "lectures", "Jazz & Blues": "jazz", "Clubkonzerte": "live_show"}  # fmt: skip
    for category, genre in expected.items():
        [event] = parse_shop(_shop(**{'"category":"Party"': f'"category":"{category}"'}), SHOP, settings, now=BEFORE)
        enriched = enricher.enrich(event) or event
        assert genre in enriched.genres, (category, enriched.genres)
