"""hypd: events sitemap and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.hypd import event_urls, page_genres, parse_event

TZ = "Asia/Jerusalem"
SITE = "https://hypd.co.il"
FIXTURES = Path(__file__).parent / "fixtures/hypd"
URL = f"{SITE}/event/infected-mushroom-national-park-open-air-2026-11-28-786898"


def test_sitemap_keeps_event_pages_from_today_on() -> None:
    xml = "".join(
        f"<url><loc>{SITE}/{p}</loc></url>"
        for p in (
            "event/old-night-2026-11-16-1",
            "event/tonight-2026-11-17-2",
            "event/fest-2027-03-01-3",
            "artist/yahel",
        )
    )
    assert event_urls(xml, SITE, date(2026, 11, 17)) == [
        f"{SITE}/event/tonight-2026-11-17-2",
        f"{SITE}/event/fest-2027-03-01-3",
    ]


def test_open_air_party_with_place_lineup_seller_and_genres() -> None:
    e = parse_event((FIXTURES / "infected-mushroom.html").read_text("utf-8"), URL, TZ)
    assert e is not None and e.source_event_id == "786898" and str(e.url) == URL
    assert e.title == "Infected Mushroom | National Park / Open Air"  # the RTL mark dropped
    assert e.start_at.isoformat() == "2026-11-28T14:30:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-11-28T23:00:00+02:00"
    assert e.venue_name == "הפארק הלאומי רמת גן" and e.city == "רמת גן" and e.address == "Ahad Ha'Am St 3"
    assert (e.latitude, e.longitude) == (32.0511471, 34.8251604) and e.country is None
    assert (
        e.min_price == Decimal(249)
        and e.extra["purchase_url"] == "https://zygo.co.il/event/381158075244786898/ZF10b15u3"
    )
    assert e.artists[0] == "Infected Mushroom" and "Yahel" in e.artists
    assert e.images and all("opengraph-image" not in i for i in e.images)
    assert {"טראנס", "פסטיבלים"} <= set(e.source_genres) and "רמת גן" not in e.source_genres
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(e)
    assert {"trance", "festival"} <= set(enriched.genres)


def test_place_and_age_chips_are_no_genre_and_nature_hub_is_a_nature_party() -> None:
    html = (
        '<div class="event_tags__x"><a href="/genre/techno">טכנו</a><span>רמת ישי</span><span>18+</span><span>אוויר פתוח</span></div>'
        '<nav class="ev-portals"><a href="/genre/techno">מסיבות טכנו</a><a href="/nature">מסיבות טבע</a><a href="/north">צפון</a></nav>'
    )
    assert page_genres(html, [], {"/nature": "nature party"}) == ["טכנו", "nature party", "אוויר פתוח"]


def test_event_abroad_in_the_venue_zone() -> None:
    node = {
        "@context": "https://schema.org", "@type": "MusicEvent", "name": "ARTBAT Dubai", "url": f"{SITE}/event/artbat-2026-11-28-9",
        "startDate": "2026-11-28T22:00:00+02:00",
        "location": {"@type": "Place", "name": "Ushuaia", "address": {"addressLocality": "דובאי", "addressCountry": "AE"}},
    }  # fmt: skip
    page = f'<script type="application/ld+json">{json.dumps(node)}</script>'
    e = parse_event(page, node["url"], TZ)
    assert e is not None and e.country == "AE" and e.time_zone == "Asia/Dubai"
    assert e.start_at.isoformat() == "2026-11-28T22:00:00+04:00"
    assert e.extra == {}
