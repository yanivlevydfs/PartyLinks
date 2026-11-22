"""Concert (concert-events.com) parser and listing tests against real event pages (Nov 2026)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import concertevents
from partylinks.scrapers.concertevents import ConcertEventsScraper, ConcertEventsSettings, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "concertevents"
TZ = "Asia/Jerusalem"
SETTINGS = ConcertEventsSettings(cron="55 8,20 * * *")
BASE = "https://concert-events.com/events/"


def _page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def test_event_with_price_and_genres() -> None:
    url = BASE + "גלית-סדן-רויטל-נחמני-פטריקס-פארק-56"
    event = parse_event(_page("galit"), url, SETTINGS, TZ)
    assert event is not None
    assert event.source_event_id == "draft-9e1218f827443324484046"
    assert event.title == "גלית סדן ורויטל נחמני"
    assert event.start_at == datetime(2026, 11, 21, tzinfo=ZoneInfo(TZ)) and not event.time_known
    assert event.end_at is None
    assert (event.venue_name, event.address, event.city) == ("פטריקס פארק", "פקריס 6 רחובות", "רחובות")
    assert (event.latitude, event.longitude) == (None, None)  # the site has no coordinates
    assert event.source_genres == ["מזרחית", "ים תיכוני"]
    assert event.artists == ["גלית סדן", "רויטל נחמני"]
    assert event.min_price == event.max_price == Decimal(50)
    assert event.images and event.images[0].startswith("https://images.concert-events.com/")
    assert event.extra == {"phone": "055-2523332"}  # no ticket page: sold by phone

    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and "mizrahi" in enriched.genres


def test_event_without_address() -> None:
    url = BASE + "תמיר-סולטנה-מאי-פדידה-מתן-דדון-טראמפ-קלאב"
    event = parse_event(_page("trump"), url, SETTINGS, TZ)
    assert event is not None
    assert event.start_at == datetime(2026, 11, 19, 22, 30, tzinfo=ZoneInfo(TZ)) and event.time_known
    assert (event.venue_name, event.address, event.city) == ("טראמפ קלאב", None, None)
    assert event.min_price is None
    assert event.extra.get("phone") == "053-2250699"


def test_weekend_package_city_only() -> None:
    url = BASE + "רביד-גואטה-קוקו-מאילת-אילת"
    event = parse_event(_page("eilat"), url, SETTINGS, TZ)
    assert event is not None
    assert event.start_at == datetime(2026, 11, 26, tzinfo=ZoneInfo(TZ)) and not event.time_known
    assert event.end_at == datetime(2026, 11, 28, tzinfo=ZoneInfo(TZ))
    # The city alone in streetAddress is no street address; no venue named.
    assert (event.venue_name, event.address, event.city) == (None, None, "אילת")
    assert event.artists == ["רביד גואטה", "קוקו מאילת"]
    assert event.source_genres  # the site's chips


def test_ticket_link_drops_contact_hosts() -> None:
    assert concertevents._ticket_link("https://wa.me/972500000000", SETTINGS) is None
    assert concertevents._ticket_link("https://www.instagram.com/club", SETTINGS) is None
    assert concertevents._ticket_link("tel:050", SETTINGS) is None
    assert concertevents._ticket_link("https://go-out.co/event/1", SETTINGS) == "https://go-out.co/event/1"


def test_sitemap_event_urls_keep_order() -> None:
    sitemap = (
        "<urlset><url><loc>https://concert-events.com/events</loc></url>"
        f"<url><loc>{BASE}a</loc></url><url><loc>{BASE}b</loc></url><url><loc>{BASE}a</loc></url>"
        "<url><loc>https://concert-events.com/venues/x</loc></url></urlset>"
    )
    assert concertevents.sitemap_event_urls(sitemap, "/events/") == [BASE + "a", BASE + "b"]


def test_scrape_reads_only_the_upcoming_tail() -> None:
    """The sitemap is in date order: a binary search finds the first current page, past ones are not read."""
    now = datetime.now(ZoneInfo(TZ))
    starts = [now - timedelta(days=60 - i) for i in range(40)] + [now + timedelta(days=i + 1) for i in range(8)]
    urls = [f"{BASE}e{i}" for i in range(len(starts))]
    sitemap = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    scraper = ConcertEventsScraper(SETTINGS, HttpSettings(max_retries=0), TZ)
    fetched: list[int] = []

    async def get_text(url: str, **kwargs: object) -> str:
        assert url == SETTINGS.site_url + SETTINGS.sitemap_path
        return sitemap

    async def fetch(url: str) -> ScrapedEvent:
        i = urls.index(url)
        fetched.append(i)
        return ScrapedEvent(source="concertevents", source_event_id=str(i), url=url, title=f"e{i}", start_at=starts[i])

    scraper.get_text = get_text  # type: ignore[method-assign]
    scraper._fetch = fetch  # type: ignore[method-assign]

    async def collect() -> list[ScrapedEvent]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    events = asyncio.run(collect())
    assert sorted(int(e.source_event_id) for e in events) == list(range(40, 48))
    assert len(set(fetched)) == len(fetched)  # no page read twice
    assert len([i for i in fetched if i < 40]) <= 6  # only the search's probes touch past pages
