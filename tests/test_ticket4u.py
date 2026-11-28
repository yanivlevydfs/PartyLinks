"""Ticket4U / Shefa: the Eventim reader on the T4U sitemap, Eventim's search API for refused pages, Zappa left out."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.eventim import EventimScraper, EventimSettings, pick_urls
from partylinks.scrapers.ticket4u import Ticket4uScraper

T4U = "https://www.ticket4u.co.il/event/"
# Real Eventim search-API answers (Nov 2026), shared with test_eventim: the city_names=* seed and one city's page.
API = json.loads((Path(__file__).parent / "fixtures/eventim/api_products.json").read_text("utf-8"))
CHALLENGE = "<html><head><title>Access Denied</title></head><body>Reference #18.abc _abck sec-if-cpt</body></html>"


def _t4u_url(product: dict) -> str:
    """The same show's ticket4u.co.il page: Eventim's path (same slug and id) on the T4U host."""
    return T4U + product["link"].split("/event/", 1)[1]


def test_ticket4u_is_the_eventim_reader_on_the_t4u_sitemap() -> None:
    scraper, eventim = build_scraper("ticket4u", Settings()), build_scraper("eventim", Settings())
    assert isinstance(scraper, Ticket4uScraper) and isinstance(scraper, EventimScraper)
    s, e = scraper.settings, eventim.settings
    assert isinstance(s, EventimSettings) and isinstance(e, EventimSettings)
    assert (s.site_url, s.sitemap_path) == ("https://www.ticket4u.co.il", "/staticsite/sitemap/T4U/events1_he.xml.gz")
    # Its pages are Eventim's (same ids): eventim.co.il's API fills the refused ones.
    assert s.api_url and (s.api_url, s.api_web_id) == (e.api_url, e.api_web_id)
    assert not s.city_paths  # their items would overwrite the API's (no artwork, no coordinates)
    assert not s.vpn and not s.israel_proxy  # direct: our VPN gets Akamai's challenge at once
    assert s.skip_url_words == e.skip_url_words  # the Zappa shows: read by zappa


def test_zappa_shows_are_left_to_zappa() -> None:
    s = build_scraper("ticket4u", Settings()).settings
    assert isinstance(s, EventimSettings)
    urls = [
        T4U + "גידי-גוב-זאפה-חיפה-22239443/",
        T4U + "%D7%96%D7%90%D7%A4%D7%94-%D7%94%D7%A8%D7%A6%D7%9C%D7%99%D7%94-1/",  # encoded "זאפה הרצליה"
        T4U + "הופעה-אמפי-שוני-2/",
        T4U + "המכבים-חנוכה-2026-תיאטרון-סינמה-סיטי-22091874/",
    ]
    assert pick_urls(urls, s.url_words, s.skip_url_words) == urls[3:]


def test_refused_pages_come_from_the_eventim_api_as_ticket4u_events() -> None:
    scraper = build_scraper("ticket4u", Settings())
    assert isinstance(scraper, Ticket4uScraper)
    scraper.settings.skip_url_words = []  # the fixture's two live shows are at Zappa Haifa
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    pages = [_t4u_url(p) for p in API["city"]["products"]]
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path.endswith(".xml.gz"):
            assert request.url.path == "/staticsite/sitemap/T4U/events1_he.xml.gz"
            locs = "".join(f"<url><loc>{u}</loc></url>" for u in pages)
            return httpx.Response(200, text=f"<urlset>{locs}</urlset>", headers={"content-type": "application/xml"})
        if request.url.host == "public-api.eventim.com":
            assert request.url.params["webId"] == "web__eventim-co-il"
            return httpx.Response(200, json=API["seed"] if request.url.params["city_names"] == "*" else API["city"])
        return httpx.Response(200, text=CHALLENGE, headers={"content-type": "text/html; charset=utf-8"})  # Akamai

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert set(hosts) == {"www.ticket4u.co.il", "public-api.eventim.com"}  # never eventim.co.il's pages
    assert not scraper.failures  # every refused page is in the API: nothing lost
    # The cancelled show (6.12) and the past one (Gevatron, 20.6) are dropped; Gidi Gov is a ticket4u
    # listing of its T4U page, with the API's coordinates and artwork.
    assert [(e.title, e.source, e.source_event_id) for e in events] == [("גידי גוב", "ticket4u", "22239443")]
    gidi = events[0]
    assert str(gidi.url).startswith("https://www.ticket4u.co.il/event/") and gidi.latitude is not None and gidi.images


def test_site_genres_and_the_kids_default_for_an_uncategorised_show() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    start = datetime(2026, 12, 14, 17, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))

    def event(title: str, genres: list[str]) -> ScrapedEvent:
        return ScrapedEvent(
            source="ticket4u", source_event_id="1", url=T4U + "x-1/", title=title, start_at=start,
            venue_name="היכל התרבות כפר יונה", city="כפר יונה", source_genres=genres,
        )  # fmt: skip

    live = enricher.enrich(event("גידי גוב", ["הופעות חיות", "אמנים ישראלים"]))
    assert live is not None and "live_show" in live.genres and "kids" not in live.genres
    # A Hanukkah kids' show whose API product has no category (Nov 2026): the source's default, and its holiday
    # beside it (a holiday says when, not what: enrichment.extra_genres).
    donuts = enricher.enrich(event("חנות סופגניות קטנה ומצחיקה - חנוכה 2026", []))
    assert donuts is not None and donuts.genres == ["hanukkah", "kids"]


def test_a_region_city_becomes_the_town_at_the_venue_s_pin() -> None:
    from partylinks.config import CitySettings
    from partylinks.scrapers.ticket4u import _REGION, town_at

    towns = (
        CitySettings(key="haifa", he="חיפה", en="Haifa", center=(32.80, 35.00), radius_m=10000),
        CitySettings(
            key="kiryat-motzkin", he="קריית מוצקין", en="Kiryat Motzkin", center=(32.837, 35.077), radius_m=2500
        ),
    )
    assert _REGION.search("חיפה והצפון") and _REGION.search("תל אביב והמרכז")
    assert not _REGION.search("חיפה") and not _REGION.search("ראשון לציון")
    assert town_at(32.8447, 35.0799, towns) == "קריית מוצקין"  # היכל תיאטרון מוצקין: inside Haifa's radius too
    assert town_at(32.803, 34.985, towns) == "חיפה"  # the Haifa Auditorium
    assert town_at(33.172, 35.611, towns) is None  # Kfar Blum: no town here within reach
    assert town_at(None, None, towns) is None


def test_markup_in_an_event_name_is_left_out() -> None:
    # 23.11.2026: "במה אחת, שני מופעים\n<br> \nמהמט דאש אריאל לוי" (pageMeta's event_name, the API's name too)
    from partylinks.scrapers.eventim_api import plain_name

    assert plain_name("במה אחת, שני מופעים\n<br> \nמהמט דאש אריאל לוי") == "במה אחת, שני מופעים מהמט דאש אריאל לוי"
    assert plain_name("  ") is None and plain_name(None) is None
