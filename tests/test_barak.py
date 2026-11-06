"""Barak Tickets: city-page ItemList listing and the Eventim event-page parser under the barak name.

Snippets trimmed from real pages (Wayback captures, June / November 2025)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.barak import last_page, parse_city
from partylinks.scrapers.eventim import parse_event

SITE = "https://www.barak-tickets.co.il"
TZ = "Asia/Jerusalem"


def _item(name: str, item_url: str, offer_url: str | None) -> dict[str, object]:
    offers = {"@type": "AggregateOffer", "lowPrice": "0.0", "priceCurrency": "ILS"} | (
        {"url": offer_url} if offer_url else {}
    )
    return {"@type": "ListItem", "item": {"@type": "MusicEvent", "name": name, "url": item_url, "offers": offers}}


ITEMS = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    "name": "ירושלים",
    "itemListElement": [
        _item("אורי ברויר", f"{SITE}/artist/אורי-ברויר/", f"{SITE}/event/אורי-ברויר-זאפה-ירושלים-19677219/"),
        _item("פסטיבל", f"{SITE}/artist/פסטיבל/", f"{SITE}/event/פסטיבל-בריכת-הסולטן-19836133/?affiliate=HBK"),
        _item("פסטיבל", f"{SITE}/artist/פסטיבל/", f"{SITE}/event/פסטיבל-בריכת-הסולטן-19836133/"),  # repeated
        _item("ללא הצעה", f"{SITE}/event/מופע-בית-מזיא-20153388/", None),  # event url on the item only
        _item("אמן בלבד", f"{SITE}/artist/אמן/", None),  # no event page: skipped
    ],
}
CITY = (
    '<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>'
    f'<script id="eventMarkup" type="application/ld+json">{json.dumps(ITEMS, ensure_ascii=False)}</script>'
    '<a href="/city/D799-306/?page=2" data-page="2">2</a><a href="/city/D799-306/?page=3" data-page="3">3</a>'
)

EVENT_URL = f"{SITE}/event/מחווה-לעוזי-חיטמן-20-שנה-בלעדיו-היכל-התרבות-נשר-20658045/"
EVENT = (
    '<meta property="og:image" content="https://www.barak-tickets.co.il/obj/media/IL-eventim/teaser/evo/artwork/2025/uzitribute-artwork.png" />'
    '<script>window.dataLayer.push({"env_affiliate_abbrv":"HBK","event":"pageMeta",'
    '"event_series_genre":["הופעות חיות"],"event_serie_ID":"3978685","event_series_sub_genre":["אמנים ישראלים","מוסיקה"],'
    '"event_name":"מחווה לעוזי חיטמן - 20 שנה בלעדיו","event_id":"20658045","event_city_name":"נשר",'
    '"event_venue_name":"היכל התרבות נשר","event_venue_id":"364417","event_date":"2025-11-13T20:30:00+02:00",'
    '"event_booking_type_default":"seatmap","event_tickets_available":"1",'
    '"event_date_end":"2025-11-13T23:30:00+02:00"});</script>'
)


def test_city_page_lists_event_pages_by_id() -> None:
    assert parse_city(CITY, SITE) == {
        "19677219": f"{SITE}/event/אורי-ברויר-זאפה-ירושלים-19677219/",
        "19836133": f"{SITE}/event/פסטיבל-בריכת-הסולטן-19836133/",
        "20153388": f"{SITE}/event/מופע-בית-מזיא-20153388/",
    }
    assert last_page(CITY) == 3
    assert last_page("<p>one page</p>") == 1


def test_event_page_reads_as_eventim() -> None:
    event = parse_event(EVENT, EVENT_URL, TZ, "barak")
    assert event is not None
    assert (event.source, event.source_event_id, event.title, event.venue_name, event.city) == (
        "barak", "20658045", "מחווה לעוזי חיטמן - 20 שנה בלעדיו", "היכל התרבות נשר", "נשר",
    )  # fmt: skip
    assert event.start_at is not None and event.start_at.isoformat() == "2025-11-13T20:30:00+02:00"
    assert event.source_genres == ["הופעות חיות", "אמנים ישראלים", "מוסיקה"]
    assert event.image_url and event.image_url.endswith("uzitribute-artwork.png")
    assert (event.tickets, event.is_sold_out, event.extra["booking_type"]) == ([], False, "seatmap")


def test_seat_map_page_prices_from_jsonld_offers() -> None:
    # Live page, Nov 2026 (pageMeta + JSON-LD kept): a seat-map event has no ticket rows, its price
    # categories are the AggregateOffer's itemOffered.offers.
    page = (Path(__file__).parent / "fixtures/barak/seatmap_21325690.html").read_text("utf-8")
    event = parse_event(page, f"{SITE}/event/the-beatles-symphony-מרכז-הקונגרסים-חיפה-21325690/", TZ, "barak")
    assert event is not None and event.title == "THE BEATLES SYMPHONY"
    assert [(t.name, t.price) for t in event.tickets][:2] == [
        ("רמה 1", Decimal("349.00")),
        ("רמה 2", Decimal("299.00")),
    ]
    assert (event.min_price, event.max_price) == (Decimal("189.00"), Decimal("349.00")) and not event.is_sold_out


def test_refused_page_falls_back_to_the_listing_item() -> None:
    from partylinks.scrapers.barak import BarakSettings
    from partylinks.scrapers.eventim_listing import listing_event

    item = {
        "@type": "ChildrensEvent", "name": "קרקס הובה קובה", "startDate": "2026-11-05T17:00:00.000+02:00",
        "endDate": "2026-11-05T18:00:00.000+02:00",
        "location": {"@type": "Place", "name": "אוהל הקרקס הגדול", "address": {"addressLocality": "באר שבע"}},
        "offers": {"@type": "AggregateOffer", "lowPrice": "0.0", "availability": "https://schema.org/InStock"},
    }  # fmt: skip
    genres = BarakSettings(cron="0 0 * * *", site_url=SITE, city_paths=[]).type_genres
    event = listing_event("21884752", f"{SITE}/event/קרקס-21884752/", item, TZ, "barak", genres)
    assert event is not None and event.source_event_id == "21884752"
    assert (event.title, event.venue_name, event.city) == ("קרקס הובה קובה", "אוהל הקרקס הגדול", "באר שבע")
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-05T17:00:00+02:00"
    assert event.end_at is not None
    assert event.min_price is None and not event.is_sold_out  # "0.0" is the listing's blank
    assert event.source_genres == ["ילדים"]
    assert listing_event("1", SITE, {"name": "x"}, TZ, "barak", genres) is None  # no date


def test_widget_links_carry_every_page_s_events_and_their_group() -> None:
    from partylinks.scrapers.barak import widget_links

    # The listing widget's prerendered buttons (live page 2 of the Haifa region, Nov 2026, trimmed).
    page = (
        '<serp-widget><a class="btn" href="/artist/שומרי-הדרקונים/?affiliate=HBK">הצג</a>'
        '<a class="btn link" href="/event/שומרי-הדרקונים-אולם-יגור-22032075/?affiliate=HBK">כרטיסים</a>'
        '<a class="btn link" href="/event/שומרי-הדרקונים-היכל-התרבות-כרמיאל-22163190/?affiliate=HBK">כרטיסים</a>'
        '<a class="btn" href="/eventseries/תמיר-גל-3699617/?affiliate=HBK">הצג</a>'
        '<a class="btn link" href="/event/%D7%AA%D7%9E%D7%99%D7%A8-%D7%92%D7%9C-22030761/">כרטיסים</a></serp-widget>'
    )
    assert widget_links(page, SITE) == {
        "22032075": (f"{SITE}/event/שומרי-הדרקונים-אולם-יגור-22032075/", "/artist/שומרי-הדרקונים/"),
        "22163190": (f"{SITE}/event/שומרי-הדרקונים-היכל-התרבות-כרמיאל-22163190/", "/artist/שומרי-הדרקונים/"),
        "22030761": (f"{SITE}/event/תמיר-גל-22030761/", "/eventseries/תמיר-גל-3699617/"),
    }
    assert widget_links("<p>no events</p>", SITE) == {}


def test_group_page_dates_become_listing_items() -> None:
    from partylinks.scrapers.barak import BarakSettings, parse_group
    from partylinks.scrapers.eventim_listing import listing_event

    sub = {
        "@type": "ChildrensEvent", "name": "שומרי הדרקונים", "startDate": "2026-11-28T11:00:00.000+02:00",
        "endDate": "2026-11-28T12:00:00.000+02:00", "image": f"{SITE}/obj/media/IL-eventim/galery/222x222/2026/dragons.jpg",
        "location": {"@type": "EventVenue", "name": "אולם יגור", "address": {"addressLocality": "קיבוץ יגור"}},
        "offers": [{"@type": "Offer", "availability": "https://schema.org/SoldOut",
                    "url": f"{SITE}/event/שומרי-הדרקונים-אולם-יגור-22032075/"}],
    }  # fmt: skip
    series = {"@type": "EventSeries", "name": "שומרי הדרקונים", "subEvent": [sub, {"name": "no date", "offers": []}]}
    page = f'<script type="application/ld+json">{json.dumps(series, ensure_ascii=False)}</script>'
    groups = parse_group(page, SITE)
    assert list(groups) == ["22032075"]
    url, item = groups["22032075"]
    genres = BarakSettings(cron="0 0 * * *", site_url=SITE, city_paths=[]).type_genres
    event = listing_event("22032075", url, item, TZ, "barak", genres)
    assert event is not None and (event.title, event.venue_name, event.city) == (
        "שומרי הדרקונים",
        "אולם יגור",
        "קיבוץ יגור",
    )
    assert event.is_sold_out and event.source_genres == ["ילדים"]
    assert item["image"].endswith("dragons.jpg")


def test_run_reads_every_page_and_builds_refused_events_from_group_pages() -> None:
    import asyncio

    import httpx

    from partylinks.config import Settings
    from partylinks.scrapers import build_scraper
    from partylinks.scrapers.barak import BarakScraper

    sub = {
        "@type": "MusicEvent", "name": "תמיר גל", "startDate": "2027-01-10T21:30:00+02:00",
        "image": f"{SITE}/obj/media/tamirgal-teaser.jpg", "location": {"@type": "EventVenue", "name": "זאפה חיפה"},
        "offers": [{"@type": "Offer", "url": f"{SITE}/event/תמיר-גל-זאפה-חיפה-22030761/"}],
    }  # fmt: skip
    group = f'<script type="application/ld+json">{json.dumps({"@type": "EventSeries", "subEvent": [sub]})}</script>'
    page2 = '<a href="/eventseries/תמיר-גל-3699617/">הצג</a><a href="/event/תמיר-גל-זאפה-חיפה-22030761/">כרטיסים</a>'
    scraper = build_scraper("barak", Settings())
    assert isinstance(scraper, BarakScraper)
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    scraper.settings.city_paths = ["/city/x-307/"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/city/"):
            return httpx.Response(
                200, text=page2 if request.url.params.get("page") == "2" else '<a data-page="2">2</a>'
            )
        if path.startswith("/eventseries/"):
            return httpx.Response(200, text=group)
        return httpx.Response(200, text="<html>sec-if-cpt</html>")  # Akamai refuses every event page

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert [(e.source_event_id, e.title, e.venue_name, e.image_url) for e in events] == [
        ("22030761", "תמיר גל", "זאפה חיפה", f"{SITE}/obj/media/tamirgal-teaser.jpg")
    ]
    assert scraper.failures  # the refused event page: a partial run
