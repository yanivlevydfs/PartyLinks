"""Eventim: sitemap listing and event-page parsing (dataLayer pageMeta, ticket rows, JSON-LD)."""

from __future__ import annotations

import json
from decimal import Decimal

from partylinks.scrapers.eventim import parse_event, sitemap_event_urls

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
