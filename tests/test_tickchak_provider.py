"""Tickchak's provider-page lookups never go through the source's own client."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers import tickchak
from partylinks.scrapers.tickchak import TickchakScraper, TickchakSettings

PAGE = """<html><head><meta property="og:image" content="https://seller.example/p.jpg"/>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Event","name":"Show",
"offers":{"@type":"Offer","price":"120","priceCurrency":"ILS","availability":"https://schema.org/InStock"}}</script>
</head></html>"""


def test_a_provider_page_fills_artwork_and_price_through_its_own_client(monkeypatch: Any) -> None:
    from partylinks.config import Settings

    scraper = TickchakScraper(TickchakSettings.model_validate(Settings().sources["tickchak"]), HttpSettings(), "UTC")
    seen: list[str] = []

    async def fetch(s: Any, url: str) -> str:
        seen.append(url)
        return PAGE

    async def own_client(*_: Any, **__: Any) -> str:
        raise AssertionError("the source's own client must not fetch seller pages")

    monkeypatch.setattr(tickchak, "fetch_seller_page", fetch)
    monkeypatch.setattr(scraper, "get_text", own_client)
    event = ScrapedEvent(source="tickchak", source_event_id="1", url="https://t.example/1", title="Show",
                         start_at=datetime(2030, 1, 1, tzinfo=UTC))  # fmt: skip
    asyncio.run(scraper._provider_page(event, "https://seller.example/e/1", asyncio.Semaphore(1)))
    assert seen == ["https://seller.example/e/1"] and event.image_url == "https://seller.example/p.jpg"
    assert [t.price for t in event.tickets] == [120]
