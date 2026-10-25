"""Barak Tickets: city-page ItemList listing and the Eventim event-page parser under the barak name.

Snippets trimmed from real pages (Wayback captures, June / November 2025)."""

from __future__ import annotations

import json

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
