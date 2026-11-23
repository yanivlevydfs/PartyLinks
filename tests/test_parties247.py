"""Parties24/7: the home page's lists, the genre / city lists' labels, each party page's JSON-LD Event."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.parties247 import (
    Parties247Scraper,
    category_of,
    event_paths,
    listing_paths,
    parse_event,
    sitemap_paths,
    split_location,
)

SITE = "https://www.parties247.co.il"
SOON = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)


def _page(**over: Any) -> str:
    """A party page's JSON-LD Event, as the site writes it (Nov 2026)."""
    node = {
        "@context": "https://schema.org", "@type": "Event", "name": "🎉 26.11 | HAMISHI OF JUSTLOVEIT",
        "startDate": SOON.isoformat(), "eventStatus": "https://schema.org/EventScheduled",
        "location": {"@type": "Place", "name": "GOAT TLV, שדרות רוטשילד, Tel Aviv-Yafo, ישראל",
                     "address": {"@type": "PostalAddress", "streetAddress": "GOAT TLV, שדרות רוטשילד, Tel Aviv-Yafo, ישראל"}},
        "image": ["https://images.go-out.co/events/6af1e8_coverImage.jpg"],
        "description": "HAMISHI OF JUSTLOVEIT 🔥 800 איש במקום אחד",
        "offers": {"@type": "Offer", "url": "https://go-out.co/event/1795030412247?ref=a3fg911a00", "price": "87.4",
                   "priceCurrency": "ILS", "availability": "https://schema.org/InStock"},
    } | over  # fmt: skip
    return f'<html><script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script></html>'


def test_location_text_is_split_into_venue_street_and_city() -> None:
    assert split_location("GOAT TLV, שדרות רוטשילד, Tel Aviv-Yafo, ישראל") == (
        "GOAT TLV",
        "שדרות רוטשילד",
        "Tel Aviv-Yafo",
    )
    # A street address and a city: the street is the address too (geocoding reads it).
    # A street and a city: the street is the address, not a venue (no venue page per street address).
    assert split_location("בן אביגדור 17, תל אביב-יפו, ישראל") == (None, "בן אביגדור 17", "תל אביב-יפו")
    assert split_location("שדרות רוטשילד 24, תל אביב") == (None, "שדרות רוטשילד 24", "תל אביב")
    assert split_location("The Place, תל אביב") == ("The Place", None, "תל אביב")
    assert split_location("משה בקר 11") == (None, "משה בקר 11", None)
    # A town, region or country alone is the city, never a venue.
    towns = {"הרצליה", "tel aviv"}
    assert split_location("הרצליה", towns) == (None, None, "הרצליה")
    assert split_location("Tel Aviv, Israël", towns) == (None, None, "Tel Aviv")
    assert split_location("מחוז הצפון") == (None, None, None)
    assert split_location("") == (None, None, None)


def test_party_page() -> None:
    event = parse_event(_page(), SITE + "/event/hamishi-of-justloveit-november-2026", ["techno music"])
    assert event is not None
    assert (event.source, event.source_event_id) == ("parties247", "hamishi-of-justloveit-november-2026")
    assert event.title == "🎉 26.11 | HAMISHI OF JUSTLOVEIT" and event.start_at == SOON
    assert (event.venue_name, event.address, event.city) == ("GOAT TLV", "שדרות רוטשילד", "Tel Aviv-Yafo")
    assert str(event.min_price) == "87.4" and not event.is_sold_out
    assert event.extra["purchase_url"] == "https://go-out.co/event/1795030412247"  # the affiliate ref dropped
    assert event.images == ["https://images.go-out.co/events/6af1e8_coverImage.jpg"]
    assert event.source_genres == ["techno music"]
    enriched = Enricher(Settings().enrichment, "Asia/Jerusalem").enrich(event)
    assert enriched is not None and "techno" in enriched.genres


def test_cancelled_undated_and_eventless_pages_give_nothing() -> None:
    url = SITE + "/event/x"
    assert parse_event(_page(eventStatus="https://schema.org/EventCancelled"), url, []) is None
    assert parse_event(_page(startDate=None), url, []) is None
    assert parse_event("<html><title>Parties24/7</title></html>", url, []) is None


def test_lists_and_their_labels() -> None:
    home = '<a href="/day/today">היום</a><a href="/genre/techno-music">טכנו</a><a href="/about">אודות</a><a href="/event/a-1">a</a>'
    prefixes = build_scraper("parties247", Settings()).settings.listing_prefixes  # type: ignore[attr-defined]
    assert listing_paths(home, prefixes) == ["/day/today", "/genre/techno-music"]
    assert event_paths(home + '<a href="/event/a-1">again</a>') == ["/event/a-1"]
    assert category_of("/genre/techno-music", ["/genre/", "/audience/"]) == "techno music"
    assert category_of("/audience/24plus-parties", ["/genre/", "/audience/"]) == "24plus parties"
    assert category_of("/day/today", ["/genre/", "/audience/"]) is None
    xml = f"<urlset><url><loc>{SITE}/purim</loc></url><url><loc>{SITE}/about</loc></url><url><loc>{SITE}/event/%D7%90-1</loc></url></urlset>"
    assert sitemap_paths(xml, prefixes) == ["/purim"]
    assert sitemap_paths(xml, ["/event/"]) == ["/event/א-1"] and sitemap_paths(None, ["/event/"]) == []


def test_run_labels_parties_by_their_lists_and_fills_a_missing_city() -> None:
    scraper = build_scraper("parties247", Settings())
    assert isinstance(scraper, Parties247Scraper)
    scraper.http.request_delay_seconds = 0
    pages = {
        "/": '<a href="/genre/techno-music">x</a><a href="/cities/tel-aviv">x</a><a href="/event/goat-1">x</a>',
        "/genre/techno-music": '<a href="/event/goat-1">x</a>',
        "/cities/tel-aviv": '<a href="/event/street-2">x</a>',
        "/sitemaps/static.xml": f"<urlset><url><loc>{SITE}/genre/hip-hop-music</loc></url><url><loc>{SITE}/about</loc></url></urlset>",
        "/genre/hip-hop-music": '<a href="/event/goat-1">x</a>',
        "/sitemaps/events.xml": f"<urlset><url><loc>{SITE}/event/goat-1</loc></url><url><loc>{SITE}/event/purim-3</loc></url></urlset>",
        "/event/purim-3": _page(name="Purim 2027"),
        "/event/goat-1": _page(),
        "/event/street-2": _page(name="Free Alcohol", location={"@type": "Place", "name": "משה בקר 11"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[request.url.path], headers={"content-type": "text/html; charset=utf-8"})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(go())}
    # purim-3 is on no list, only in the events sitemap; hip-hop is a genre list only the static sitemap links.
    assert set(events) == {"goat-1", "street-2", "purim-3"} and not scraper.failures
    assert (
        events["goat-1"].source_genres == ["hip hop music", "techno music"] and events["goat-1"].city == "Tel Aviv-Yafo"
    )
    assert events["street-2"].city == "תל אביב-יפו"  # from the city list it is on
