"""Eventim: sitemap listing and event-page parsing (dataLayer pageMeta, ticket rows, JSON-LD)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from partylinks.config import Settings
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.base import SourceBlockedError
from partylinks.scrapers.eventim import EventimScraper, parse_event, sitemap_event_urls

TZ = "Asia/Jerusalem"
URL = "https://www.eventim.co.il/event/show-venue-22285519/"

# The real pushes break a key across lines, which strict JSON rejects.
PAGE_META = (
    '<script>window.dataLayer.push({"env_action":"","\nenv_country":"IL","event":"pageMeta",'
    '"event_name":"לירון אופיר","event_id":"22285519","event_city_name":"ירושלים",'
    '"event_venue_name":"זאפה ירושלים","event_date":"2026-12-03T21:30:00+02:00",'
    '"event_date_end":"2026-12-03T23:29:00+02:00","event_series_genre":["מופעי סטנדאפ"],'
    '"event_series_sub_genre":["מופעי בידור","סטנדאפ"],"event_tickets_available":"%s"});</script>'
)
TICKETS = """
<div data-qa="tickettype" data-tt-name="רגיל"><div data-qa="tickettypeItem-price">99.00&nbsp;₪</div></div>
<div data-qa="tickettype" data-tt-name="כסא נגיש"><div data-qa="tickettypeItem-price">99.00&nbsp;₪</div></div>
<div data-qa="tickettype" data-tt-name="VIP"><div data-qa="tickettypeItem-price">1,250.00&nbsp;₪</div></div>
"""
OG = '<meta property="og:image" content="https://www.eventim.co.il/obj/media/artwork/lirono.jpg">'


def _ld(status: str) -> str:
    node = {
        "@context": "https://schema.org", "@type": "MusicEvent", "name": "לירון אופיר", "eventStatus": status,
        "location": {"@type": "EventVenue", "name": "זאפה", "address": {"streetAddress": "דרך חברון 28"}},
        "performer": {"@type": "Person", "name": "לירון אופיר"},
    }  # fmt: skip
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


def test_sitemap_keeps_event_pages_only() -> None:
    xml = (
        b"<urlset><url><loc>https://www.eventim.co.il/event/a-22285519/</loc></url>"
        b"<url><loc>https://www.eventim.co.il/artist/a/</loc></url>"
        b"<url><loc>https://www.eventim.co.il/event/a-22285519/</loc></url></urlset>"
    )
    assert sitemap_event_urls(xml) == ["https://www.eventim.co.il/event/a-22285519/"]


def test_page_without_json_ld_uses_page_meta_and_ticket_rows() -> None:
    event = parse_event(PAGE_META % "1|0" + TICKETS + OG, URL, TZ)
    assert event is not None
    assert (event.source_event_id, event.title, event.venue_name, event.city) == (
        "22285519", "לירון אופיר", "זאפה ירושלים", "ירושלים",
    )  # fmt: skip
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-12-03T21:30:00+02:00"
    assert event.source_genres == ["מופעי סטנדאפ", "מופעי בידור", "סטנדאפ"]
    assert [(t.name, t.price) for t in event.tickets] == [
        ("רגיל", Decimal("99.00")), ("כסא נגיש", Decimal("99.00")), ("VIP", Decimal("1250.00")),
    ]  # fmt: skip
    assert (event.min_price, event.max_price, event.is_sold_out) == (Decimal("99.00"), Decimal("1250.00"), False)
    assert event.image_url == "https://www.eventim.co.il/obj/media/artwork/lirono.jpg"
    assert event.address is None and event.artists == []


def test_json_ld_adds_address_and_performer_and_drops_cancelled() -> None:
    event = parse_event(PAGE_META % "1|0" + _ld("https://schema.org/EventScheduled") + TICKETS, URL, TZ)
    assert event is not None and event.address == "דרך חברון 28" and event.artists == ["לירון אופיר"]
    assert parse_event(PAGE_META % "1|0" + _ld("https://schema.org/EventCancelled"), URL, TZ) is None


def test_unavailable_event_is_sold_out() -> None:
    event = parse_event(PAGE_META % "0|0" + TICKETS, URL, TZ)
    assert event is not None and event.is_sold_out
    assert all(t.status.value == "sold_out" for t in event.tickets)


def test_zappa_pages_go_to_the_zappa_source_only() -> None:
    from partylinks.scrapers.eventim import pick_urls

    urls = [
        "https://www.eventim.co.il/event/נונו-זאפה-ירושלים-22296371/",
        "https://www.eventim.co.il/event/%D7%96%D7%90%D7%A4%D7%94-%D7%97%D7%99%D7%A4%D7%94-1/",  # encoded
        "https://www.eventim.co.il/event/הופעה-אמפי-שוני-2/",
        "https://www.eventim.co.il/event/קולות-מן-השמיים-סטודיו-אנט-22318978/",
    ]
    words = ["זאפה", "אמפי-שוני"]
    assert pick_urls(urls, words, []) == urls[:3]
    assert pick_urls(urls, [], words) == urls[3:]


def _ld_only_page() -> str:
    node = {
        "@context": "https://schema.org", "@type": "MusicEvent", "name": "שלמה ארצי",
        "url": "https://www.eventim.co.il/event/show-venue-22285519/", "startDate": "2026-12-03T21:30:00",
        "image": "https://www.eventim.co.il/obj/media/artwork/arzi.jpg",
        "location": {"@type": "Place", "name": "היכל מנורה", "address": {"streetAddress": "יגאל אלון 51", "addressLocality": "תל אביב"},
                     "geo": {"latitude": 32.07, "longitude": 34.79}},
        "performer": {"@type": "Person", "name": "שלמה ארצי"},
        "offers": [{"@type": "Offer", "name": "יציע", "price": "199", "priceCurrency": "ILS"},
                   {"@type": "Offer", "name": "רחבה", "price": "349", "priceCurrency": "ILS"}],
    }  # fmt: skip
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


def test_page_without_page_meta_is_read_from_json_ld() -> None:
    event = parse_event(_ld_only_page(), URL, TZ, "zappa")
    assert event is not None
    assert (event.source, event.source_event_id, event.title) == ("zappa", "22285519", "שלמה ארצי")
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-03T21:30:00+02:00"
    assert (event.venue_name, event.city, event.latitude) == ("היכל מנורה", "תל אביב", 32.07)
    assert (event.min_price, event.max_price, event.artists) == (Decimal(199), Decimal(349), ["שלמה ארצי"])
    assert event.image_url == "https://www.eventim.co.il/obj/media/artwork/arzi.jpg"


CHALLENGE = "<html><head><title>Access Denied</title></head><body>Reference #18.abc _abck</body></html>"


def _run(pages: dict[str, httpx.Response]) -> tuple[list[ScrapedEvent], EventimScraper]:
    sitemap = "".join(f"<url><loc>https://www.eventim.co.il/event/{p}/</loc></url>" for p in pages)
    scraper = build_scraper("eventim", Settings())
    assert isinstance(scraper, EventimScraper)
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    scraper.settings.max_empty_streak = 3

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xml.gz"):
            return httpx.Response(200, text=f"<urlset>{sitemap}</urlset>", headers={"content-type": "application/xml"})
        return pages[request.url.path.strip("/").rsplit("/", 1)[-1]]

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    return asyncio.run(go()), scraper


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


def test_run_reads_page_meta_and_json_ld_pages() -> None:
    events, scraper = _run({"a-1": _html(PAGE_META % "1|0" + TICKETS), "b-22285519": _html(_ld_only_page())})
    assert sorted(e.title for e in events) == ["לירון אופיר", "שלמה ארצי"] and scraper.empty_pages == 0


def test_challenge_page_stops_the_run_as_blocked() -> None:
    with pytest.raises(SourceBlockedError):
        _run({"a-1": _html(CHALLENGE), "b-2": _html(CHALLENGE)})


def test_empty_pages_in_a_row_stop_the_run_as_blocked() -> None:
    with pytest.raises(SourceBlockedError, match="in a row"):
        _run({f"e-{i}": _html("<title>אירועים</title>") for i in range(5)})


def test_refused_event_pages_come_from_the_city_listing_and_the_run_is_partial() -> None:
    item = {"@type": "ListItem", "item": {
        "@type": "MusicEvent", "name": "שלמה ארצי", "startDate": "2027-01-10T21:00:00+02:00",
        "location": {"@type": "Place", "name": "היכל מנורה", "address": {"addressLocality": "תל אביב"}},
        "offers": {"@type": "AggregateOffer", "lowPrice": "199.0", "url": "https://www.eventim.co.il/event/a-1/"},
    }}  # fmt: skip
    city = f'<script type="application/ld+json">{json.dumps({"@type": "ItemList", "itemListElement": [item]})}</script>'
    scraper = build_scraper("eventim", Settings())
    assert isinstance(scraper, EventimScraper)
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    scraper.settings.city_paths = ["/city/x-1/"]
    scraper.settings.api_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xml.gz"):
            loc = "<url><loc>https://www.eventim.co.il/event/a-1/</loc></url><url><loc>https://www.eventim.co.il/event/b-2/</loc></url>"
            return httpx.Response(200, text=f"<urlset>{loc}</urlset>", headers={"content-type": "application/xml"})
        if request.url.path.startswith("/city/"):
            return _html(city)
        return _html(CHALLENGE)  # Akamai refuses every event page

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert [(e.source_event_id, e.title, e.venue_name, e.min_price) for e in events] == [
        ("1", "שלמה ארצי", "היכל מנורה", Decimal("199.0"))
    ]  # b-2 is in the sitemap only: not invented, and the run is partial so it is not unlisted either
    assert scraper.failures and "event pages" in scraper.failures[0]


def test_zappa_reads_the_zappa_club_sitemap_too() -> None:
    from partylinks.scrapers.eventim import ZappaScraper

    eventim = ["https://www.eventim.co.il/event/נונו-זאפה-ירושלים-1/", "https://www.eventim.co.il/event/מופע-היכל-2/"]
    club = [
        "https://www.zappa-club.co.il/event/נונו-זאפה-ירושלים-1/",
        "https://www.zappa-club.co.il/event/חנוכה-היכל-3/",
    ]
    scraper = build_scraper("zappa", Settings())
    assert isinstance(scraper, ZappaScraper)
    scraper.http.request_delay_seconds = 0
    scraper.settings.url_words = ["זאפה"]

    def handler(request: httpx.Request) -> httpx.Response:
        urls = club if request.url.host == "www.zappa-club.co.il" else eventim
        locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return httpx.Response(200, text=f"<urlset>{locs}</urlset>", headers={"content-type": "application/xml"})

    async def go() -> list[str]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return await scraper._list_urls()

    # Eventim's pages named for Zappa, and every show the Zappa site sells; one page per event id (the club's).
    assert asyncio.run(go()) == club


# Real answers (Nov 2026): the city_names=* seed (its city facet cut to one city) and that city's page, cut to 3 shows.
API = json.loads((Path(__file__).parent / "fixtures/eventim/api_products.json").read_text("utf-8"))


def test_refused_event_pages_come_from_the_search_api_and_the_run_stays_clean() -> None:
    scraper = build_scraper("eventim", Settings())
    assert isinstance(scraper, EventimScraper) and scraper.settings.api_url
    scraper.settings.skip_url_words = []  # the fixture's shows are at Zappa Haifa
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    pages = [p["link"] for p in API["city"]["products"]]
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xml.gz"):
            locs = "".join(f"<url><loc>{u}</loc></url>" for u in pages)
            return httpx.Response(200, text=f"<urlset>{locs}</urlset>", headers={"content-type": "application/xml"})
        if request.url.host == "public-api.eventim.com":
            city = request.url.params["city_names"]
            asked.append(city)
            return httpx.Response(200, json=API["seed"] if city == "*" else API["city"])
        return _html(CHALLENGE)  # Akamai refuses every event page

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    asyncio.run(go())  # (the fixture's shows pass their dates; the fields are checked below)
    assert asked == ["*", "חיפה והצפון"]  # the seed's city facet, then each city
    assert not scraper.failures  # every refused page is in the API: nothing lost, the run is not partial


def test_api_product_fields() -> None:
    from partylinks.scrapers.eventim_api import product_event

    gidi, gevatron, cancelled = [
        product_event(p["productId"], p["link"], p, TZ, "eventim") for p in API["city"]["products"]
    ]
    assert gidi is not None and gevatron is not None and cancelled is None
    assert (gidi.title, gidi.venue_name, gidi.city, gidi.min_price, gidi.is_sold_out) == (
        "גידי גוב", "זאפה חיפה", "חיפה והצפון", Decimal("179.0"), False,
    )  # fmt: skip
    assert gidi.start_at is not None and gidi.start_at.isoformat() == "2026-12-04T14:00:00+02:00"
    assert gidi.latitude == pytest.approx(32.7896) and gidi.artists == ["גידי גוב"]
    assert gidi.source_genres == ["הופעות חיות", "אמנים ישראלים"]
    assert gidi.images == [
        "https://www.eventim.co.il/obj/media/IL-eventim/teaser/evo/artwork/2022/gidigovnew-artwork.jpg",
        "https://www.eventim.co.il/obj/media/IL-eventim/teaser/222x222/2022/gidigovnew-teaser.jpg",
    ]
    assert gevatron.is_sold_out and gevatron.min_price is None
