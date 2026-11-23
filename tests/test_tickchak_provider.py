"""Tickchak's artwork / price lookups: its own event page through its own client, sellers' pages never."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers import tickchak
from partylinks.scrapers.tickchak import TickchakScraper, TickchakSettings, own_page_artwork

PAGE = """<html><head><meta property="og:image" content="https://seller.example/p.jpg"/>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Event","name":"Show",
"offers":{"@type":"Offer","price":"120","priceCurrency":"ILS","availability":"https://schema.org/InStock"}}</script>
</head></html>"""
# The real page of Moshe Peretz at Menora (23.11.2026), trimmed: a default illustration, his photo on his artist.
EVENT_PAGE = (Path(__file__).parent / "fixtures" / "tickchak_event_page.html").read_text(encoding="utf-8")
PHOTO = "https://static.tickchak.co.il/all/updateArtistImage_main_1745502051D90TQ3BVVxcUb2g2UCdmQ3tWUqhjSQN3d_c619LMUlvZ0uwJiW5q2SM.webp"
OWN_URL = "https://live.tickchak.co.il/shows/event/eBjWlQMmn"


def _scraper() -> TickchakScraper:
    from partylinks.config import Settings

    return TickchakScraper(TickchakSettings.model_validate(Settings().sources["tickchak"]), HttpSettings(), "UTC")


def _event(**fields: Any) -> ScrapedEvent:
    return ScrapedEvent(source="tickchak", source_event_id="eBjWlQMmn", url=OWN_URL, title="משה פרץ",
                        start_at=datetime(2030, 1, 1, tzinfo=UTC), **fields)  # fmt: skip


def test_a_provider_page_fills_artwork_and_price_through_its_own_client(monkeypatch: Any) -> None:
    scraper = _scraper()
    seen: list[str] = []
    own_pages: list[str] = []

    async def fetch(s: Any, url: str) -> str:
        seen.append(url)
        return PAGE

    async def own_client(url: str, *_: Any, **__: Any) -> str:
        assert url == OWN_URL, "the source's own client must not fetch seller pages"
        own_pages.append(url)
        return "<html><body>no Next.js data</body></html>"  # no pictures there: the seller's page is next

    monkeypatch.setattr(tickchak, "fetch_seller_page", fetch)
    monkeypatch.setattr(scraper, "get_text", own_client)
    event = _event()
    asyncio.run(scraper._provider_page(event, "https://seller.example/e/1", asyncio.Semaphore(1)))
    assert own_pages == [OWN_URL] and seen == ["https://seller.example/e/1"]
    assert event.image_url == "https://seller.example/p.jpg"
    assert [t.price for t in event.tickets] == [120]


def test_the_artists_photo_on_the_event_page_is_the_picture() -> None:
    """The event's image is the category illustration (isDefaultImage); the artist's photo is the picture."""
    assert own_page_artwork(EVENT_PAGE) == [PHOTO]


def test_own_page_artwork_skips_the_sellers_page_and_its_favicon(monkeypatch: Any) -> None:
    """Ticketmaster's page gave only its favicon: a priced event with a photo on its own page needs no seller page."""
    scraper = _scraper()

    async def own_client(url: str, *_: Any, **__: Any) -> str:
        return EVENT_PAGE

    async def seller(*_: Any) -> str:
        raise AssertionError("nothing left for the seller's page to add")

    monkeypatch.setattr(scraper, "get_text", own_client)
    monkeypatch.setattr(tickchak, "fetch_seller_page", seller)
    event = _event(min_price=219)
    asyncio.run(
        scraper._provider_page(event, "https://www.ticketmaster.co.il/event/MMM36/ALL/iw", asyncio.Semaphore(1))
    )
    assert event.images == [PHOTO] and event.image_url == PHOTO


def test_a_sellers_favicon_is_no_picture(monkeypatch: Any) -> None:
    scraper = _scraper()

    async def own_client(url: str, *_: Any, **__: Any) -> str:
        raise RuntimeError("page down")

    async def seller(*_: Any) -> str:
        return (
            '<html><head><meta property="og:image" content="https://www.ticketmaster.co.il/favicon.ico"/></head></html>'
        )

    monkeypatch.setattr(scraper, "get_text", own_client)
    monkeypatch.setattr(tickchak, "fetch_seller_page", seller)
    event = _event()
    asyncio.run(
        scraper._provider_page(event, "https://www.ticketmaster.co.il/event/MMM36/ALL/iw", asyncio.Semaphore(1))
    )
    assert event.images == [] and event.image_url is None


def test_a_stored_logo_is_looked_up_again() -> None:
    """The favicon stored on an earlier run is not "artwork read before": the event goes to the lookups."""
    scraper = _scraper()
    scraper.known_images = {"eBjWlQMmn": ["https://www.ticketmaster.co.il/favicon.ico"]}
    item = {
        "entityId": "eBjWlQMmn", "title": "משה פרץ", "url": "/shows/event/eBjWlQMmn", "date": 1798138800,
        "link": "https://www.ticketmaster.co.il/event/MMM36/ALL/iw", "minPrice": 219, "maxPrice": 499,
        "image": {"image": "https://static.tickchak.co.il/all/default.webp", "isDefaultImage": True},
    }  # fmt: skip
    missing: list[tuple[ScrapedEvent, str]] = []
    ready = scraper._events({"eBjWlQMmn": item}, {}, frozenset(), missing)
    assert ready == [] and [e.source_event_id for e, _ in missing] == ["eBjWlQMmn"]
    assert missing[0][0].images == []
