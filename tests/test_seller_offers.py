"""Seller lookup: a guide's event without a price / picture completed from its seller (no network)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.models import ScrapedEvent
from partylinks.scrapers.seller_offers import (
    SellerLookup,
    SellerLookupSettings,
    bookit_images,
    bookit_tickets,
    page_offer,
)

FIXTURES = Path(__file__).parent / "fixtures"
TZ = "Asia/Jerusalem"
# Trimmed real answer of POST https://bookit.fun/api/product/id (Nov 2026).
PRODUCT = json.loads((FIXTURES / "sellers/bookit_product.json").read_text("utf-8"))
SHOWS = (FIXTURES / "smarticket/tzavta_shows.json").read_text("utf-8")


def _event(purchase_url: str, start: datetime | None = None, **more: Any) -> ScrapedEvent:
    return ScrapedEvent(
        source="ogo",
        source_event_id="x1",
        url="https://www.ogo.co.il/he/event/x1",
        title="מופע",
        start_at=start or datetime(2026, 12, 8, 12, tzinfo=ZoneInfo(TZ)),
        extra={"purchase_url": purchase_url},
        **more,
    )


def test_bookit_price_list_of_the_day() -> None:
    tiers = bookit_tickets(PRODUCT, "2026-12-08")
    # "ילד.ה עד גיל 6: 0" beside paid tiers is not free entry.
    assert [t.price for t in tiers] == [Decimal(80), Decimal(65), Decimal(50), Decimal(220)]
    assert not any(t.is_free for t in tiers)
    # A day not on sale yet: the product's stated minimum.
    assert [(t.name, t.price) for t in bookit_tickets(PRODUCT, "2027-03-01")] == [("החל מ", Decimal(80))]
    assert bookit_tickets({}, "2026-12-08") == []
    images = bookit_images(PRODUCT)
    assert images and images[0].endswith("6fa5df3dad87428d8689f5b13505f365_original.jpeg")  # IsMain first


def _ld(*events: dict[str, Any]) -> str:
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(e)}</script>' for e in events)
    return f'<html><head><meta property="og:image" content="https://seller/logo.png">{scripts}</head></html>'


def test_seller_page_event_offers_only() -> None:
    event = {
        "@type": "Event",
        "name": "Show",
        "startDate": "2026-12-08T20:00:00+02:00",
        "image": "https://seller/show.jpg",
        "offers": [{"@type": "Offer", "price": "79.00", "priceCurrency": "ILS"}, {"@type": "Offer", "name": "VIP"}],
    }
    tiers, images = page_offer(_ld(event), "https://seller/e/1", TZ)
    assert [t.price for t in tiers] == [Decimal(79)] and images == ["https://seller/show.jpg"]  # not the logo
    assert page_offer(_ld(event, event | {"name": "Other"}), "https://seller/list", TZ) == ([], [])  # a listing
    assert page_offer("<html><body>כרטיס: 40 ₪</body></html>", "https://seller/p", TZ) == ([], [])  # no guessing


class _FakeScraper:
    timezone = TZ
    log = logging.getLogger("test")

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def get_text(self, url: str, **_: Any) -> str:
        self.asked.append(url)
        if url not in self.pages:
            raise RuntimeError("404")
        return self.pages[url]

    async def post_json(self, url: str, **_: Any) -> Any:
        self.asked.append(url)
        return PRODUCT


def test_fill_from_smarticket_and_bookit() -> None:
    scraper = _FakeScraper({"https://tzavta.smarticket.co.il/api/shows": SHOWS})
    sellers = SellerLookup(scraper, SellerLookupSettings())  # type: ignore[arg-type]
    show_page = "https://tzavta.smarticket.co.il/%D7%A9%D7%9D_%D7%94%D7%9E%D7%95%D7%A4%D7%A2"

    async def run() -> list[ScrapedEvent]:
        events = (
            _event("https://tzavta.smarticket.co.il/event/17950/"),  # the performance's own page
            _event(show_page, start=datetime(2026, 12, 31, 21, tzinfo=ZoneInfo(TZ))),  # the show's page: by start
            _event(show_page, start=datetime(2001, 1, 1, tzinfo=ZoneInfo(TZ))),  # no performance then
            _event("https://bookit.fun/product?productID=ZOw7nVOx&affiliateid=1iQmAIJ"),
        )
        return list(await asyncio.gather(*(sellers.fill(e) for e in events)))

    by_id, by_start, none, bookit = asyncio.run(run())
    assert by_id.min_price == Decimal(149) and by_id.image_url and by_id.image_url.endswith(".png")
    assert by_start.min_price == Decimal(149)
    assert none.min_price is None and not none.tickets  # nothing taken that is not this event's
    assert bookit.min_price == Decimal(50) and bookit.image_url
    assert scraper.asked.count("https://tzavta.smarticket.co.il/api/shows") == 1  # once per host and run
    assert sellers.filled == {"price": 3, "image": 3}


def test_fill_keeps_the_guides_data_and_gives_up_on_empty_hosts() -> None:
    scraper = _FakeScraper({})
    sellers = SellerLookup(scraper, SellerLookupSettings(seller_host_misses=2))  # type: ignore[arg-type]
    done = _event("https://bookit.fun/product?productID=ZOw7nVOx", min_price=Decimal(30), images=["https://a/b.jpg"])

    async def run() -> None:
        assert await sellers.fill(done) is done  # priced and pictured: the seller is not asked
        for n in range(4):
            assert (await sellers.fill(_event(f"https://cityhall.example/e/{n}"))).min_price is None

    asyncio.run(run())
    assert scraper.asked == ["https://cityhall.example/e/0", "https://cityhall.example/e/1"]
    off = SellerLookup(scraper, SellerLookupSettings(seller_lookup=False))  # type: ignore[arg-type]
    assert asyncio.run(off.fill(_event("https://cityhall.example/e/9"))).min_price is None
