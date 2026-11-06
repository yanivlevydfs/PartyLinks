"""ZOA House (zoatlv.co.il) parser against its real pages (October 2026)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import HttpSettings, Settings
from partylinks.dedup import url_key
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import slugify
from partylinks.scrapers import zoatlv
from partylinks.search import MONTHS

FIXTURES = Path(__file__).parent / "fixtures" / "zoatlv"
TZ = "Asia/Jerusalem"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_zoatlv_listing_pages_and_sellers() -> None:
    settings = Settings()
    cfg = zoatlv.ZoaTlvSettings.model_validate(settings.sources["zoatlv"])
    listing = _read("events.html")
    assert zoatlv.last_page(listing) == 7
    cards = zoatlv.parse_listing(listing)
    assert len(cards) == 30
    events = [zoatlv.parse_card(c, cfg, TZ) for c in cards]
    assert all(events)

    keren = next(e for e in events if e and e.title == "דרור קרן | סטנדאפ")
    assert keren.start_at == datetime(2026, 10, 30, 21, 0, tzinfo=ZoneInfo(TZ))
    assert keren.latitude and keren.city == "תל אביב-יפו" and keren.venue_name == "בית ציוני אמריקה"
    assert keren.source_genres == ["הופעה", "סטנדאפ ובידור"] and (keren.extra or {})["hall"] == "מרתה"
    assert keren.images and keren.images[0].startswith("https://zoatlv.co.il/wp-content/")

    zoatlv.add_page(keren, _read("event.html"), cfg.seller_hosts)
    assert "בבקשה לא להגיע" in (keren.description or "")
    # The seller's link reads as Leaan's own listing URL, so dedup ties the two.
    leaan = settings.sources["leaan"]
    assert leaan is not None
    leaan_url = leaan["site_url"] + leaan["event_page_path"].format(slug=slugify("דרור קרן"), event_id=6998)
    assert url_key((keren.extra or {})["purchase_url"]) == url_key(str(type(keren.url)(leaan_url)))

    enriched = Enricher(settings.enrichment, TZ).enrich(keren)
    assert enriched and "stand_up" in enriched.genres and enriched.in_target_city

    # ZOA's own EASYTIX box office: the link points at easytix.co.il, where easytix lists it.
    workshop = next(e for e in events if e and e.title.startswith("לעלות על הבמה"))
    zoatlv.add_page(workshop, _read("event_easytix.html"), cfg.seller_hosts)
    assert (workshop.extra or {})["purchase_url"] == "https://easytix.co.il/event/ZOA-TLV/TAMAR"


def test_zoatlv_card_without_date_is_skipped() -> None:
    cfg = zoatlv.ZoaTlvSettings.model_validate(Settings().sources["zoatlv"])
    card = dict.fromkeys(("url", "title", "subtitle", "day", "month", "time", "hall", "tags", "image"), "")
    assert zoatlv.parse_card({**card, "url": "https://zoatlv.co.il/event/x/", "title": "x"}, cfg, TZ) is None


def test_zoatlv_price_from_the_sellers_json_ld() -> None:
    # tickets.comedybar.net (a Smarticket box office) page, trimmed (November 2026): JSON-LD offer 85 ₪.
    assert [(t.name, t.price) for t in zoatlv.seller_tickets(_read("seller_comedybar.html"))] == [("כרטיס", 85)]
    seller = "https://tickets.comedybar.net/iframe/x/?id=6384"
    listing = """<a class="event-loop" href="https://zoatlv.co.il/event/show/"><div class="event-date">
        <span class="day">5</span><span class="my">נובמבר 2099</span></div><span class="event-time">21:30</span>
        <div class="event-titles"><h3>עושים צחוק</h3></div></a>"""
    pages = {
        "/event/": listing,
        "/event/show/": f'<div class="section-content"><div class="content"><p>מרתון</p></div></div>'
        f'<a class="event-link" href="{seller}">לאתר האירוע</a>',
        "/iframe/x/": _read("seller_comedybar.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    cfg = zoatlv.ZoaTlvSettings.model_validate(Settings().sources["zoatlv"])
    scraper = zoatlv.ZoaTlvScraper(cfg, HttpSettings(request_delay_seconds=0, backoff_seconds=0), TZ)
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    [event] = asyncio.run(run())
    assert [t.price for t in event.tickets] == [85]


def test_zoatlv_sitemap_pages_the_archive_lacks() -> None:
    # The archive stops at page 6; the event sitemap lists the rest (one future, one past, one stale).
    card = """<a class="event-loop" href="https://zoatlv.co.il/event/listed/"><div class="event-date">
        <span class="day">5</span><span class="my">נובמבר 2099</span></div><div class="event-titles"><h3>בכרטיס</h3></div></a>"""

    def page(day: str, month: str) -> str:
        return f"""<h1 class="title1 stroke">יוגה שבועית</h1><div class="event-details row"><div class="event-info">
            <div class="event-date"><span class="day">{day}</span><span class="my">{month}</span></div>
            <h4 class="hall-name">מרתה</h4><h4 class="event-time"><label>שעת התחלה:</label>19:30</h4></div>
            <div class="event-img"><img src="https://zoatlv.co.il/wp-content/uploads/y.jpg"></div></div>
            <div class="more-events"><a class="more-event" href="x"><div class="event-date">
            <span class="day">1</span><span class="my">ינואר 2000</span></div></a></div>"""

    fresh = datetime.now(ZoneInfo(TZ)).isoformat()
    soon = datetime.now(ZoneInfo(TZ)) + timedelta(days=60)  # within retention.max_future_days
    month = next(name for name, number in MONTHS.items() if number == soon.month)
    pages = {
        "/event/": card,
        "/sitemap_index.xml": f"<sitemap><loc>https://zoatlv.co.il/event-sitemap4.xml</loc><lastmod>{fresh}</lastmod></sitemap>"
        "<sitemap><loc>https://zoatlv.co.il/post-sitemap.xml</loc><lastmod>{fresh}</lastmod></sitemap>",
        "/event-sitemap4.xml": "".join(
            f"<url><loc>https://zoatlv.co.il{path}</loc><lastmod>{when}</lastmod></url>"
            for path, when in [
                ("/event/", fresh),  # the archive itself
                ("/event/listed/", fresh),  # already on the archive
                ("/event/yoga/", fresh),
                ("/event/old/", fresh),
                ("/event/stale/", "2019-01-01T00:00:00+00:00"),
            ]
        ),
        "/event/yoga/": page(str(soon.day), f"{month} {soon.year}"),
        "/event/old/": page("3", "מרץ 2020"),
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    cfg = zoatlv.ZoaTlvSettings.model_validate(Settings().sources["zoatlv"])
    scraper = zoatlv.ZoaTlvScraper(cfg, HttpSettings(request_delay_seconds=0, backoff_seconds=0), TZ)
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(run())}
    assert set(events) == {"listed", "yoga"}  # the past page dropped, the stale one never read
    assert "/event/stale/" not in asked and "/post-sitemap.xml" not in asked
    yoga = events["yoga"]
    assert yoga.start_at == soon.replace(hour=19, minute=30, second=0, microsecond=0) and yoga.title == "יוגה שבועית"
    assert (yoga.extra or {})["hall"] == "מרתה" and yoga.images == ["https://zoatlv.co.il/wp-content/uploads/y.jpg"]
