"""Glatticket parser tests on real hub search items and event pages (November 2026)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import glatticket

FIXTURES = Path(__file__).parent / "fixtures" / "glatticket"
ITEMS = {item["eid"]: item for item in json.loads((FIXTURES / "search.json").read_text(encoding="utf-8"))}
TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
CFG = glatticket.GlatticketSettings(cron="0 0 * * *")


def page(eid: int) -> str:
    return (FIXTURES / f"event_{eid}.html").read_text(encoding="utf-8")


def parsed(eid: int, **changes: object) -> ScrapedEvent:
    event = glatticket.parse_item({**ITEMS[eid], **changes}, CFG, TZ)
    assert event is not None
    return event


def test_item_kids_show_with_its_page() -> None:
    show = parsed(115938)
    assert (show.source_event_id, str(show.url), show.extra["purchase_url"]) == (
        "115938",
        "https://glatticket.co.il/115938",  # no referral query: the page OGO and the municipality link to
        "https://glatticket.co.il/115938",
    )
    assert show.title == "הנס - המסע לאי הניסים - נהריה"
    assert show.start_at == datetime(2026, 12, 3, 17, 0, tzinfo=IL)
    assert show.end_at == datetime(2026, 12, 3, 20, 0, tzinfo=IL)
    assert (show.venue_name, show.city, show.source_genres) == ("היכל התרבות נהריה", None, ["הצגות ילדים"])
    assert show.image_url and show.image_url.startswith("https://static.tickchak.co.il/all/hofbImgMobile_")
    assert len(show.images) == 2  # banner and share image
    glatticket.apply_page(show, page(115938), str(show.url))
    assert (show.latitude, show.longitude, show.address, show.city) == (33.0085361, 35.0980514, "נהריה", "נהריה")
    assert show.organizer == "ארגון תפארת."
    assert [(t.name, t.price) for t in show.tickets] == [
        ("כרטיס נגיש", Decimal("20.00")),
        ("כרטיס גברים", Decimal("20.00")),
        ("כרטיס נשים", Decimal("20.00")),
    ]
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert {"kids", "theatre"} <= set(enriched.genres)
    assert enriched.min_price == Decimal("20.00")


def test_page_street_address_and_town() -> None:
    show = parsed(116566)  # sold by the municipal company; the list names the hall only
    assert (show.venue_name, show.city) == ("היכל תרבות רמות", "ירושלים")  # venue_cities
    glatticket.apply_page(show, page(116566), str(show.url))
    assert (show.latitude, show.longitude) == (31.8171446, 35.1928856)
    assert show.address == "רח' אַבְרָהָם רֵיקָנָטי, 10, ירושלים"
    assert show.city == "ירושלים"
    assert show.organizer == "החברה העירונית אריאל"
    assert {t.price for t in show.tickets} == {Decimal("50.00")}


def test_screenshot_only_artwork_and_ashkelon() -> None:
    evening = parsed(117645)
    assert evening.images and "mobileScreenshot_" in evening.images[0]  # nothing else: the page's picture is kept
    glatticket.apply_page(evening, page(117645), str(evening.url))
    assert (evening.address, evening.city) == ("יצחק בן צבי, 12, אשקלון", "אשקלון")
    assert [(t.name, t.price) for t in evening.tickets] == [("כרטיס כניסה", Decimal("20.00"))]


def test_women_series_without_page_data() -> None:
    series = parsed(113282)  # its page redirects to a Tickchak LIVE series page: no pin, no offers
    assert (series.venue_name, series.city, series.source_genres) == ("בנייני האומה", "ירושלים", ["הופעות לנשים"])
    glatticket.apply_page(series, "<html><body>no map here</body></html>", str(series.url))
    assert (series.latitude, series.city, series.tickets) == (None, "ירושלים", [])
    assert {"live_show", "women_only"} <= set(Enricher(Settings().enrichment, TZ).enrich(series).genres)


def test_artwork_order_puts_screenshots_last() -> None:
    item = {
        **ITEMS[115938],
        "image": {"image": "https://static.tickchak.co.il/all/mobileScreenshot_1.jpeg"},
        "fbImg": "https://static.tickchak.co.il/caps/115938_screenshot-1.jpeg",
        "fbImgMobile": "https://static.tickchak.co.il/all/hofbImgMobile_2.jpeg",
        "fbImgPost": "not a url",
    }
    event = glatticket.parse_item(item, CFG, TZ)
    assert event is not None
    assert event.images == [
        "https://static.tickchak.co.il/all/hofbImgMobile_2.jpeg",
        "https://static.tickchak.co.il/all/mobileScreenshot_1.jpeg",
        "https://static.tickchak.co.il/caps/115938_screenshot-1.jpeg",
    ]


def test_not_events() -> None:
    hub = ITEMS[107334]
    assert glatticket.parse_item(hub, CFG, TZ) is None
    assert glatticket.sub_hub(hub, CFG.hub_domain) == "hEALNqOnD"
    assert glatticket.sub_hub({**hub, "url": "https://live.tickchak.co.il/hX?_ref=1"}, CFG.hub_domain) is None
    base = ITEMS[117555]
    assert glatticket.parse_item({**base, "date": None, "timeStart": None}, CFG, TZ) is None  # a club, a package
    assert glatticket.parse_item({**base, "eventCategory": 1945}, CFG, TZ) is None  # season subscriptions
    assert glatticket.parse_item({**base, "type": "givechak"}, CFG, TZ) is None  # a donation page
    assert glatticket.parse_item({**base, "title": " "}, CFG, TZ) is None
    # An end before the start is no end; an unknown category gets no label.
    odd = parsed(117555, timeEnd=base["timeStart"] - 60, eventCategory=844)
    assert (odd.end_at, odd.source_genres, odd.city) == (None, [], "ירושלים")


def test_read_pin_edge_cases() -> None:
    link = '<a href="https://www.google.com/maps?q={}">'
    assert glatticket.read_pin(link.format("32.09%2C34.88+%28HaMaccabim+Street%2C+5%2C+Petah+Tikva%29")) == (
        32.09,
        34.88,
        "HaMaccabim Street, 5, Petah Tikva",
    )
    assert glatticket.read_pin(link.format("31.86%2C35.16")) == (31.86, 35.16, None)
    assert glatticket.read_pin(link.format("0%2C0+%28x%29")) is None
    assert glatticket.read_pin(link.format("abc")) is None
    assert glatticket.read_pin("<p>no map</p>") is None


def _item(eid: int, days: float, **extra: object) -> dict[str, object]:
    start = int(time.time() + days * 86400)
    return {"eid": eid, "type": "ticket", "title": f"מופע {eid}", "timeStart": start, "date": start,
            "timeEnd": start + 7200, "venue": {"title": "מערת צדקיהו"}, "eventCategory": 1,
            "url": f"https://glatticket.co.il/{eid}?_ref=1", "isPastEvent": False, **extra}  # fmt: skip


def test_scrape_reads_hubs_until_past_and_pages() -> None:
    main = [
        _item(2, 5),
        {**ITEMS[107334]},  # a sub-hub, read once although both hubs list each other
        _item(1, 1),
        _item(3, 3, timeStart=None, date=None),  # no date: no event
    ]
    second = [_item(4, 9), _item(5, -40, isPastEvent=True), _item(6, -50, isPastEvent=True)]
    sub = [_item(7, 2), {**ITEMS[107334], "url": "https://tiferet.glatticket.co.il/hKpLyNdxl", "hubId": "hKpLyNdxl"}]
    requests: list[str] = []
    page_html = page(115938).replace("/115938/form", "/1/form")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/hub/hKpLyNdxl/searchData":
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json={0: main, len(main): second}.get(offset, []))
        if request.url.path == "/hub/hEALNqOnD/searchData":
            return httpx.Response(200, json=sub if request.url.params["offset"] == "0" else [])
        if request.url.path == "/1":
            return httpx.Response(200, text=page_html)
        return httpx.Response(404)

    scraper = glatticket.GlatticketScraper(CFG, Settings().http, TZ)
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert sorted(e.source_event_id for e in events) == ["1", "2", "4", "7"]  # no past, undated or hub items
    first = next(e for e in events if e.source_event_id == "1")
    assert (first.latitude, first.city) == (33.0085361, "נהריה")  # its page read
    assert [t.price for t in first.tickets] == [Decimal("20.00")] * 3
    # The main hub stops at the page holding a past item; the sub-hub's link back is not read again.
    searches = [r for r in requests if "searchData" in r]
    assert searches == [
        "/hub/hKpLyNdxl/searchData?text=&offset=0",
        "/hub/hKpLyNdxl/searchData?text=&offset=4",
        "/hub/hEALNqOnD/searchData?text=&offset=0",
        "/hub/hEALNqOnD/searchData?text=&offset=2",
    ]
    assert not scraper.failures  # the other events' 404 pages are gone pages, not faults


def test_config_block() -> None:
    from partylinks.scrapers import build_scraper

    scraper = build_scraper("glatticket", Settings())
    assert isinstance(scraper, glatticket.GlatticketScraper)
    assert scraper.settings.site_url == "https://home.glatticket.co.il"
    assert "ועד עובדי" in scraper.settings.skip_title_words  # a workers' committee's own evening is closed
