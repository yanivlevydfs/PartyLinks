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


def test_area_is_no_city() -> None:
    # Dead Sea shows: "עין בוקק, אזור ים המלח" and no addressLocality. "אזור" alone is the town of Azor.
    page = _page("galit").replace(
        '"streetAddress":"פקריס 6 רחובות","addressLocality":"רחובות"', '"streetAddress":"עין בוקק, אזור ים המלח"'
    )
    event = parse_event(page, BASE + "x", SETTINGS, TZ)
    assert event is not None and event.city == "ים המלח"


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


def test_listing_links_every_upcoming_event() -> None:
    """/events: the first cards, then an index linking the rest; the menu's /events link is no event."""
    page = _page("listing")
    urls = concertevents.listing_event_urls(page, SETTINGS)
    assert len(urls) == 15  # the fixture keeps 3 of the 24 cards and the index of the other 12
    assert urls[0] == BASE + "לצלילי-הבוזוקי-המעיין-בית-הבירה-חולון"
    assert urls[3] == BASE + "צביקה-אברהם-גוקר"  # the index follows the cards, in date order
    assert urls[-1] == BASE + "מופע-מחווה-לדקלון-וצלילי-הכרם-היכל-התרבות-פתח-תקווה"
    assert all(u.startswith(BASE) and len(u) > len(BASE) for u in urls)
    assert concertevents.listing_total(page, SETTINGS) == 36  # "מציגים 24 מתוך 36 אירועים"
    assert concertevents.listing_total("<html><body><p>אין אירועים</p></body></html>", SETTINGS) is None


# 40 past events, then 8 upcoming, in date order (as the sitemap lists them).
_NOW = datetime.now(ZoneInfo(TZ))
_STARTS = [_NOW - timedelta(days=60 - i) for i in range(40)] + [_NOW + timedelta(days=i + 1) for i in range(8)]


def _scrape(
    sitemap_urls: list[str], listed: list[str], *, listing_fails: bool = False, sitemap_fails: bool = False
) -> tuple[list[ScrapedEvent], list[int]]:
    """Run scrape() with the listing and sitemap given; page e<i> starts at _STARTS[i]."""
    sitemap = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in sitemap_urls) + "</urlset>"
    listing = "<html><body>" + "".join(f'<a href="{u.removeprefix(SETTINGS.site_url)}">x</a>' for u in listed)
    listing += f"<p>מציגים {len(listed)} מתוך {len(listed)} אירועים</p></body></html>"
    scraper = ConcertEventsScraper(SETTINGS, HttpSettings(max_retries=0), TZ)
    fetched: list[int] = []

    async def get_text(url: str, **kwargs: object) -> str:
        if url == SETTINGS.site_url + SETTINGS.listing_path:
            if listing_fails:
                raise RuntimeError("listing down")
            return listing
        assert url == SETTINGS.site_url + SETTINGS.sitemap_path
        if sitemap_fails:
            raise RuntimeError("sitemap down")
        return sitemap

    async def fetch(url: str) -> ScrapedEvent:
        i = int(url.removeprefix(BASE + "e"))
        fetched.append(i)
        return ScrapedEvent(source="concertevents", source_event_id=str(i), url=url, title=f"e{i}", start_at=_STARTS[i])

    scraper.get_text = get_text  # type: ignore[method-assign]
    scraper._fetch = fetch  # type: ignore[method-assign]

    async def collect() -> list[ScrapedEvent]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    return asyncio.run(collect()), fetched


def test_scrape_reads_only_the_upcoming_tail() -> None:
    """The sitemap is in date order: a binary search finds the first current page, past ones are not read."""
    urls = [f"{BASE}e{i}" for i in range(len(_STARTS))]
    events, fetched = _scrape(urls, [], listing_fails=True)
    assert sorted(int(e.source_event_id) for e in events) == list(range(40, 48))
    assert len(set(fetched)) == len(fetched)  # no page read twice
    assert len([i for i in fetched if i < 40]) <= 6  # only the search's probes touch past pages


def test_scrape_adds_listed_events_the_sitemap_lacks() -> None:
    """The sitemap lags (23.11.2026: 35 of the 36 listed): the newest listed pages are read too."""
    urls = [f"{BASE}e{i}" for i in range(len(_STARTS))]
    sitemap_urls = urls[:46]  # e46 and e47 are listed on /events only
    listed = urls[40:]
    events, fetched = _scrape(sitemap_urls, listed)
    assert sorted(int(e.source_event_id) for e in events) == list(range(40, 48))
    assert len(set(fetched)) == len(fetched)  # a page in both lists is read once
    assert len([i for i in fetched if i < 40]) <= 6


def test_scrape_listing_alone_when_the_sitemap_fails() -> None:
    urls = [f"{BASE}e{i}" for i in range(len(_STARTS))]
    events, fetched = _scrape(urls, urls[40:], sitemap_fails=True)
    assert sorted(int(e.source_event_id) for e in events) == list(range(40, 48))
    assert all(i >= 40 for i in fetched)  # no past page read at all
