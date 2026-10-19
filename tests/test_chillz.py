"""Chillz: JSON-LD parsing, sitemap discovery and checkpoint handling (no network)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from partylinks.config import Settings
from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers import build_scraper
from partylinks.scrapers.base import SourceBlockedError
from partylinks.scrapers.chillz import ChillzScraper, parse_page

TZ = "Asia/Jerusalem"

EVENT_LD = {
    "@context": "https://schema.org",
    "@type": "MusicEvent",
    "name": "Deep Sunday | TLV",
    "description": "<p>House all night.</p><p>LINE UP</p><p>DJ ONE</p>",
    "startDate": "2026-11-01T23:00:00+02:00",
    "endDate": "2026-11-02T05:00:00+02:00",
    "eventStatus": "https://schema.org/EventScheduled",
    "image": ["https://cdn.chillz.example/flyer.jpg", {"@type": "ImageObject", "url": "https://cdn.chillz.example/wide.jpg"}],
    "location": {
        "@type": "Place",
        "name": "The Block",
        "address": {"@type": "PostalAddress", "streetAddress": "Shalma 157", "addressLocality": "Tel Aviv-Yafo"},
        "geo": {"@type": "GeoCoordinates", "latitude": 32.05, "longitude": 34.77},
    },
    "performer": [{"@type": "Person", "name": "DJ ONE"}],
    "organizer": {"@type": "Organization", "name": "Deep Crew"},
    "offers": [
        {"@type": "Offer", "name": "Free before 00:30", "price": "0", "priceCurrency": "ILS",
         "availability": "https://schema.org/InStock"},
        {"@type": "Offer", "name": "Early bird", "price": "60", "priceCurrency": "ILS",
         "availability": "https://schema.org/SoldOut"},
        {"@type": "Offer", "name": "Regular", "price": "90", "priceCurrency": "ILS",
         "availability": "https://schema.org/InStock"},
    ],
}  # fmt: skip


def page(ld: object) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'


def test_parses_event_with_all_offers() -> None:
    event = parse_page(page(EVENT_LD), "https://chillz.com/en/deep-sunday", TZ)

    assert event is not None and event.source_event_id == "deep-sunday"
    assert (event.venue_name, event.city, event.latitude) == ("The Block", "Tel Aviv-Yafo", 32.05)
    assert [t.kind for t in event.tickets] == [TicketKind.FREE, TicketKind.PAID, TicketKind.PAID]
    assert event.tickets[1].status is TierStatus.SOLD_OUT
    assert (event.min_price, event.max_price) == (Decimal(0), Decimal(90))
    assert event.images == ["https://cdn.chillz.example/flyer.jpg", "https://cdn.chillz.example/wide.jpg"]
    assert event.artists == ["DJ ONE"] and event.organizer == "Deep Crew"


def test_non_event_page_is_skipped() -> None:
    assert parse_page(page({"@type": "Organization", "name": "Some producer"}), "https://chillz.com/en/x", TZ) is None


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://chillz.com/en</loc></url>
  <url><loc>https://chillz.com/en/explore/tlv-tickets</loc></url>
  <url><loc>https://chillz.com/en/deep-sunday</loc><lastmod>2099-01-01T00:00:00Z</lastmod></url>
  <url><loc>https://chillz.com/en/old-party</loc><lastmod>2001-01-01T00:00:00Z</lastmod></url>
  <url><loc>https://chillz.com/he/deep-sunday</loc></url>
</urlset>"""


def run_with(handler: httpx.MockTransport) -> list:  # type: ignore[type-arg]
    scraper = build_scraper("chillz", Settings())
    assert isinstance(scraper, ChillzScraper)
    scraper.settings.inbox_dir = None  # live-site behaviour only; never read the real inbox
    scraper.http.request_delay_seconds = 0

    async def go() -> list:  # type: ignore[type-arg]
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=handler)
            return [e async for e in scraper.scrape()]

    return asyncio.run(go())


def test_collects_recent_locale_pages_when_access_is_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(200, text=page(EVENT_LD))

    events = run_with(httpx.MockTransport(handler))
    assert [e.url.path for e in events] == ["/en/deep-sunday"]


def test_checkpoint_stops_the_run_without_retrying() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(429, text="<title>Vercel Security Checkpoint</title>")

    with pytest.raises(SourceBlockedError):
        run_with(httpx.MockTransport(handler))
    assert calls == ["/sitemap.xml", "/en/deep-sunday"]  # one probe, no retries


def saved_page(ld: object, url: str) -> str:
    """What Chrome's Ctrl+S produces: the original URL in a comment plus the rendered DOM."""
    return f"<!-- saved from url=({len(url):04d}){url} -->\n" + page(ld)


def test_inbox_imports_browser_saved_pages_then_stops_at_checkpoint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "deep.html").write_text(saved_page(EVENT_LD, "https://chillz.com/en/deep-sunday"), encoding="utf-8")
    (tmp_path / "other-site.html").write_text(saved_page(EVENT_LD, "https://example.com/x"), encoding="utf-8")

    scraper = build_scraper("chillz", Settings())
    assert isinstance(scraper, ChillzScraper)
    scraper.settings.inbox_dir = tmp_path
    scraper.http.request_delay_seconds = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(429, text="<title>Vercel Security Checkpoint</title>")

    collected: list = []  # type: ignore[type-arg]

    async def go() -> None:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async for event in scraper.scrape():
                collected.append(event)

    with pytest.raises(SourceBlockedError):
        asyncio.run(go())
    assert [e.source_event_id for e in collected] == ["deep-sunday"]  # inbox survives the block
    assert collected[0].tickets and str(collected[0].url) == "https://chillz.com/en/deep-sunday"
    assert scraper.failures == ["inbox/other-site.html: not a saved chillz.com page (no original URL found)"]
