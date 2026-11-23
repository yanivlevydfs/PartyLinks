"""Chillz: JSON-LD parsing, sitemap discovery and checkpoint handling (no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from partylinks.config import Settings
from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers import build_scraper
from partylinks.scrapers.base import SourceBlockedError
from partylinks.scrapers.chillz import ChillzScraper, _date_line, listed_events, parse_page

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


def test_event_without_offers_saying_free_entry_gets_a_free_tier() -> None:
    """Callas karaoke night (real page, trimmed): no Offer, the description says "כניסה עלינו"."""
    html = (Path(__file__).parent / "fixtures/chillz/karaoke_free.html").read_text("utf-8")
    event = parse_page(html, "https://chillz.com/en/karaoke-night-ExqP", TZ)
    assert event is not None and [(t.kind, t.price) for t in event.tickets] == [(TicketKind.FREE, Decimal(0))]
    assert event.min_price == 0
    assert parse_page(page({**EVENT_LD, "offers": []}), "https://chillz.com/en/x", TZ).tickets == []  # type: ignore[union-attr]


def test_non_event_page_is_skipped() -> None:
    assert parse_page(page({"@type": "Organization", "name": "Some producer"}), "https://chillz.com/en/x", TZ) is None


FIXTURES = Path(__file__).parent / "fixtures/chillz"


def test_event_without_venue_is_read_from_the_rendered_page() -> None:
    """A venue-less event has only a WebPage node in its JSON-LD (real page, trimmed: "הפותקאסט לייב")."""
    html = (FIXTURES / "no_venue_night.html").read_text("utf-8")
    event = parse_page(html, "https://chillz.com/en/tkjk", TZ)

    assert event is not None and (event.title, event.source_event_id, event.organizer) == ("הפותקאסט לייב", "tkjk", "Circle")
    assert (event.start_at.isoformat(), event.end_at.isoformat()) == ("2026-12-24T19:30:00+02:00", "2026-12-24T22:30:00+02:00")  # type: ignore[union-attr]
    assert event.time_known and event.venue_name is None and event.images
    # Agorot plus the fee the buyer pays (as the JSON-LD Offers of other events show it).
    assert [(t.name, t.price, t.status) for t in event.tickets] == [
        ("10 הראשונות", Decimal("78.68"), TierStatus.ON_SALE),
        ("אני על זה", Decimal("94.41"), TierStatus.COMING_SOON),
        ("לקחתי את הזמן אבל אני על זה", Decimal("110.15"), TierStatus.COMING_SOON),
    ]
    assert "רבנו חננאל 27" in (event.description or "")
    # The same page under another slug (an organizer page listing it) is not this event.
    assert parse_page(html, "https://chillz.com/en/circel", TZ) is None


def test_festival_day_tickets_keep_their_own_day() -> None:
    """Three-day festival without a venue (real page, trimmed): each day ticket names its day and hours."""
    event = parse_page((FIXTURES / "no_venue_festival.html").read_text("utf-8"), "https://chillz.com/en/EFtI-2026-EFtI", TZ)

    assert event is not None and event.start_at.isoformat() == "2026-11-25T20:00:00+02:00"  # type: ignore[union-attr]
    assert event.end_at is not None and event.end_at.isoformat() == "2026-12-11T16:00:00+02:00"
    days = [(t.description or "").split("\n")[0] for t in event.tickets]
    assert days[:3] == ["25 November 2026 20:00–2:00", "26 November 2026 19:30–2:00", "27 November 2026 12:00–16:00"]
    assert event.tickets[3].name == "כרטיס פסטיבל" and event.max_price == Decimal("122.11")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Thursday, 31 December 2026 · 21:00", ("2026-12-31T21:00:00", None)),
        ("Friday, 4 December 2026 · 23:00 – 05:00", ("2026-12-04T23:00:00", "2026-12-05T05:00:00")),
        ("Wednesday, 25 November 2026 · 8:00 PM – Friday, 27 November 2026 · 4:00 PM", ("2026-11-25T20:00:00", "2026-11-27T16:00:00")),
        ("Sunday, 3 January 2027", ("2027-01-03", None)),
        ("Coming soon", (None, None)),
    ],
)
def test_hero_date_line(line: str, expected: tuple[str | None, str | None]) -> None:
    assert _date_line(line) == expected


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


# The scrape drops past events (and dates years ahead), so the run tests need a party next month.
_NEXT_MONTH = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
UPCOMING_LD = EVENT_LD | {"startDate": _NEXT_MONTH.isoformat(), "endDate": (_NEXT_MONTH + timedelta(hours=6)).isoformat()}


def test_collects_recent_locale_pages_when_access_is_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(200, text=page(UPCOMING_LD))

    events = run_with(httpx.MockTransport(handler))
    assert [e.url.path for e in events] == ["/en/deep-sunday"]


def flight_page(*grids: tuple[str, list[dict[str, str]]]) -> str:
    """A listing page as Chillz renders it: each grid's cards in the Next.js flight data, then its section."""
    rows = "".join(json.dumps({"items": cards, "gridPageSize": 12, "sectionId": sid}, separators=(",", ":")) for sid, cards in grids)
    return f"<html><body><script>self.__next_f.push([1,{json.dumps(rows)}])</script></body></html>"


def card(slug: str, starts: datetime) -> dict[str, str]:
    return {"id": f"evt_{slug}", "slug": slug, "startsAt": starts.isoformat(), "endsAt": "$undefined"}


def test_listing_cards_are_the_upcoming_ones() -> None:
    """An organizer page's "org-upcoming" grid is read; its "org-past" grid and cards already over are not."""
    html = flight_page(
        ("org-upcoming", [card("mama-fest", _NEXT_MONTH), card("last-week", _NEXT_MONTH - timedelta(days=40))]),
        ("org-past", [card("old-mama", _NEXT_MONTH)]),
    )
    assert listed_events(html, TZ) == [("mama-fest", _NEXT_MONTH)]
    assert listed_events(page(EVENT_LD), TZ) == []


def test_events_only_an_organizer_page_lists_are_fetched_after_the_sitemap() -> None:
    """The sitemap lacked Mama Africa's April 2027 festival, which its organizer page listed (Nov 2026)."""
    sitemap = SITEMAP.replace("</urlset>", "<url><loc>https://chillz.com/en/deep-crew</loc></url></urlset>")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        pages = {
            "/sitemap.xml": sitemap,
            "/en": flight_page(("dsec_home", [card("deep-sunday", _NEXT_MONTH)])),
            "/en/deep-crew": flight_page(
                ("org-upcoming", [card("mama-fest", _NEXT_MONTH), card("deep-sunday", _NEXT_MONTH)]),
                ("org-past", [card("old-mama", _NEXT_MONTH)]),
            ),
        }
        return httpx.Response(200, text=pages.get(request.url.path) or page(UPCOMING_LD))

    events = run_with(httpx.MockTransport(handler))
    assert [e.url.path for e in events] == ["/en/deep-sunday", "/en/mama-fest"]
    assert calls.count("/en/deep-sunday") == 1 and "/en/old-mama" not in calls


def test_an_event_that_ended_an_hour_ago_is_left_out() -> None:
    """Yesterday evening's party is over: no day of grace (two such pages were kept, Nov 2026)."""
    ended = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    over = EVENT_LD | {"startDate": (ended - timedelta(hours=4)).isoformat(), "endDate": ended.isoformat()}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(200, text=page(over))

    assert run_with(httpx.MockTransport(handler)) == []


def test_checkpoint_stops_the_run_without_retrying() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(429, text="<title>Vercel Security Checkpoint</title>")

    with pytest.raises(SourceBlockedError):
        run_with(httpx.MockTransport(handler))
    assert calls == ["/sitemap.xml", "/en"]  # one probe (the home page's listing), no retries


def saved_page(ld: object, url: str) -> str:
    """What Chrome's Ctrl+S produces: the original URL in a comment plus the rendered DOM."""
    return f"<!-- saved from url=({len(url):04d}){url} -->\n" + page(ld)


def test_inbox_imports_browser_saved_pages_then_stops_at_checkpoint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "deep.html").write_text(saved_page(UPCOMING_LD, "https://chillz.com/en/deep-sunday"), encoding="utf-8")
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
