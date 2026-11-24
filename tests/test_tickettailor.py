"""Ticket Tailor: organizers from links, a box office's event pages, and each page's schema.org Events."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from partylinks.scrapers.tickettailor import TicketTailorSettings, event_links, organizers, parse_page

CFG = TicketTailorSettings(cron="0 0 * * *", organizers=["GilliApterComedy"])
URL = "https://www.tickettailor.com/events/gilliaptercomedy/2475703"


def _page(title: str, events: list[dict[str, Any]]) -> str:
    graph = [
        {"@type": "Organization", "@id": "https://www.tickettailor.com/#organization", "name": "Ticket Tailor"},
        {
            "@type": "Organization",
            "@id": "https://www.tickettailor.com/events/gilliaptercomedy#organizer",
            "name": "GILLI APTER COMEDY",
        },
        *events,
    ]
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph})
    return (
        f'<html><head><title>{title}</title></head><body><script type="application/ld+json">{ld}</script></body></html>'
    )


TEL_AVIV: dict[str, Any] = {
    "@type": "Event",
    "@id": "https://www.tickettailor.com/events/gilliaptercomedy/2470466#event-9263323",
    "name": "The Hilarious Deep Amazing Tel Aviv Comedy Show with Gilli Apter",
    "description": "Gilli Apter is one of South Africa’s top comedians",
    "image": ["https://uploads.tickettailorassets.com/c_scale,w_1920/v1/production/userfiles/a.png"],
    "location": {
        "@type": "Place",
        "name": "אוזןבר | Ozenbar | 48 King George St, Tel Aviv-Yafo, Israel",
        "address": {
            "streetAddress": "אוזןבר | Ozenbar | 48 King George St, Tel Aviv-Yafo, Israel",
            "addressCountry": "IL",
        },
    },
    "startDate": "2026-11-24T20:00:00+02:00",
    "endDate": "2026-11-24T22:00:00+02:00",
    "offers": [],
}

FRANKFURT: dict[str, Any] = {
    "@type": "Event",
    "@id": URL + "#event-9279726",
    "name": "The Hilarious Deep Amazing Frankfurt Comedy Show",
    "location": {
        "@type": "Place",
        "address": {
            "streetAddress": "Kulturhaus FFM | Pfingstweidstraße 2, 60316 Frankfurt am Main, Germany",
            "addressCountry": "DE",
        },
    },
    "startDate": "2027-02-04T20:00:00+01:00",
    "endDate": "2027-02-04T00:00:00+01:00",
    "offers": [{"@type": "Offer", "name": "General Admission", "price": 30, "priceCurrency": "EUR"}],
}


def test_organizers_from_links() -> None:
    links = [
        "https://www.tickettailor.com/events/gilliaptercomedy/2470466",
        "https://www.tickettailor.com/checkout/view-event/id/9408119/",
        "https://buytickets.at/yehudi1/2543540",
        "https://www.tickettailor.com/events/kii",
        "https://ozentelaviv.com/event/gilliapter/",
    ]
    assert organizers(links, CFG) == ["gilliaptercomedy", "yehudi1", "kii"]


def test_event_links() -> None:
    page = (
        '<a href="&#x2F;events&#x2F;gilliaptercomedy&#x2F;2430290">x</a>'
        '<a href="/events/gilliaptercomedy/2475703/select-date?widget=true">y</a>'
        '<a href="/events/gilliaptercomedy/2430290">again</a><a href="/events/gilliaptercomedy">home</a>'
    )
    assert event_links(page, "gilliaptercomedy", CFG) == [
        "https://www.tickettailor.com/events/gilliaptercomedy/2430290",
        "https://www.tickettailor.com/events/gilliaptercomedy/2475703",
    ]


def test_israel_sold_out() -> None:
    page = _page("Sold out – The Hilarious Deep Amazing Tel Aviv Comedy Show – אוזןבר", [TEL_AVIV])
    [event] = parse_page(page, URL, "gilliaptercomedy", CFG, "Asia/Jerusalem")
    assert event.source_event_id == "9263323"
    assert event.start_at is not None and event.end_at is not None
    assert event.start_at.isoformat() == "2026-11-24T20:00:00+02:00"
    assert event.end_at.isoformat() == "2026-11-24T22:00:00+02:00"
    assert (event.venue_name, event.city, event.country) == ("אוזןבר", "Tel Aviv-Yafo", None)
    assert event.address == "48 King George St, Tel Aviv-Yafo, Israel"
    assert event.is_sold_out
    assert event.tickets == []
    assert event.organizer == "GILLI APTER COMEDY"
    assert [str(i) for i in event.images] == [
        "https://uploads.tickettailorassets.com/c_scale,w_1920/v1/production/userfiles/a.png"
    ]


def test_abroad_series_with_price() -> None:
    second = FRANKFURT | {"@id": URL + "#event-9279727", "startDate": "2027-02-05T00:00:00+01:00"}
    page = _page("Buy tickets – Frankfurt", [FRANKFURT, second])
    first, other = parse_page(page, URL, "gilliaptercomedy", CFG, "Asia/Jerusalem")
    assert (first.venue_name, first.city, first.country, first.time_zone) == (
        "Kulturhaus FFM",
        "Frankfurt am Main",
        "DE",
        "Europe/Berlin",
    )
    assert first.end_at is None  # "00:00" the same day: no end
    assert first.time_known and not other.time_known
    assert (first.min_price, first.currency, first.tickets[0].currency) == (Decimal(30), "EUR", "EUR")
    assert not first.is_sold_out
    assert other.source_event_id == "9279727"


def test_places_without_a_town() -> None:
    cfg = CFG.model_copy(update={"organizer_cities": {"kii": "New York"}})
    la = TEL_AVIV | {"location": {"address": {"streetAddress": "Los Angeles, CA", "addressCountry": "US"}}}
    sky = TEL_AVIV | {
        "location": {"address": {"streetAddress": "SkyBar Rooftop", "postalCode": "Aventura", "addressCountry": "US"}}
    }
    hub = TEL_AVIV | {
        "location": {"name": "Kii Hub in LES", "address": {"streetAddress": "Kii Hub in LES", "addressCountry": "US"}}
    }
    assert [e.city for e in parse_page(_page("x", [la]), URL, "yehudi1", cfg, "Asia/Jerusalem")] == ["Los Angeles"]
    assert [e.city for e in parse_page(_page("x", [sky]), URL, "yehudi1", cfg, "Asia/Jerusalem")] == ["Aventura"]
    [event] = parse_page(_page("x", [hub]), URL, "kii", cfg, "Asia/Jerusalem")
    assert (event.venue_name, event.city, event.country) == ("Kii Hub in LES", "New York", "US")
