from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import posh

POSH_LISTED = {
    "id": "6ad6a2f03b1ff88a95e40b53",
    "title": "END OF THE WORLD - HALLOWEEN EDITION",
    "url": "end-of-the-world",
    "imageUrl": "https://images.posh.vip/originals/x",
    "startDate": "2026-10-31T02:00:00.000Z",
    "endDate": "2026-10-31T07:00:00.000Z",
    "timezone": "America/Toronto",
    "venueName": "O LOUNGE",
    "minTicketPrice": 29.13,
    "groupName": "5ONE4.EVENTS",
    "lineup": [],
}
POSH_PAGE = (
    'self.__next_f.push([1,"{\\"venueAddress\\":\\"1458 Rue Crescent, Montréal, QC H3G 2B6, Canada\\",'
    '\\"location\\":{\\"lat\\":45.4976672,\\"lng\\":-73.5771263},\\"description\\":\\"Biggest party\\",'
    '\\"currency\\":\\"CAD\\",\\"isAttendee\\":false}"])'
)


def test_posh_event_keeps_venue_zone_and_place():
    e = posh.parse_event(POSH_LISTED, posh.rsc_text(POSH_PAGE), "https://posh.vip/e/end-of-the-world")
    assert e is not None
    assert (e.country, e.city, e.time_zone, e.currency) == ("CA", "Montréal", "America/Toronto", "CAD")
    assert (e.latitude, e.longitude, e.description) == (45.4976672, -73.5771263, "Biggest party")
    s = Settings()
    enriched = Enricher(s.enrichment, s.timezone).enrich(e)
    assert enriched is not None
    assert enriched.time_zone == "America/Toronto"  # the source's zone, not the country's guess
    assert {"nightclubs", "holidays"} <= set(enriched.genres)


def test_posh_categories_from_title():
    assert posh.categories("Saturday Night") == ["nightclubs"]
    assert posh.categories("Sunday Brunch Market") == ["shopping", "food"]


def test_posh_sitemap_keeps_recent_event_pages_only():
    xml = (
        "<urlset><url><loc>https://posh.vip/e/new</loc><lastmod>2026-11-05T08:41:17.167Z</lastmod></url>"
        "<url><loc>https://posh.vip/e/stale</loc><lastmod>2026-07-01T00:00:00.000Z</lastmod></url>"
        "<url><loc>https://posh.vip/e/no-lastmod</loc></url>"
        "<url><loc>https://posh.vip/g/a-group</loc><lastmod>2026-11-05T00:00:00.000Z</lastmod></url></urlset>"
    )
    assert posh.sitemap_urls(xml, datetime(2026, 9, 1, tzinfo=UTC)) == ["https://posh.vip/e/new"]


def test_posh_page_alone_gives_the_event():
    page = (
        'self.__next_f.push([1,"{\\"groupName\\":\\"Alcarizzo\\",\\"eventData\\":{\\"eventId\\":\\"6aed\\",'
        '\\"url\\":\\"alcarizzo-orlando\\",\\"name\\":\\"Alcarizzo: Orlando\\",\\"flyer\\":\\"https://images.posh.vip/x\\",'
        '\\"start\\":\\"2026-12-05T22:00:00.000Z\\",\\"startUtc\\":\\"2026-12-06T03:00:00.000Z\\",'
        '\\"endUtc\\":\\"2026-12-06T07:00:00.000Z\\",\\"venueName\\":\\"\\",\\"location\\":{\\"lat\\":0,\\"lng\\":0},'
        '\\"timezone\\":\\"America/New_York\\",\\"lineup\\":[{\\"name\\":\\"DJ A\\"}],\\"minPriceWithFees\\":17.49}}"])'
    )
    text, url = posh.rsc_text(page), "https://posh.vip/e/alcarizzo-orlando"
    e = posh.parse_event(posh.page_listing(text) or {}, text, url)
    assert e is not None and (e.source_event_id, e.title, e.organizer) == ("6aed", "Alcarizzo: Orlando", "Alcarizzo")
    assert e.start_at == datetime(2026, 12, 6, 3, tzinfo=UTC) and e.time_zone == "America/New_York"  # 22:00 EST
    assert (e.artists, e.min_price, e.image_url) == (["DJ A"], Decimal("17.49"), "https://images.posh.vip/x")
    assert e.latitude is None and e.longitude is None  # 0, 0: place revealed later
    assert posh.page_listing(posh.rsc_text('self.__next_f.push([1,"{\\"notFound\\":1}"])')) is None  # deleted


def test_posh_tickets_of_an_event_listed_without_a_price():
    # events.getEventTickets, Nov 2026: a paid show, an RSVP event, door prices in the names.
    def answer(*tickets):
        return {"result": {"data": {"ticketGroups": [{"ungrouped": True, "tickets": list(tickets)}]}}}

    paid = posh.parse_tickets(answer({"name": "Default Ticket", "price": 21.83, "totalPrice": 25}), "USD")
    assert [(t.name, t.price, t.is_free) for t in paid] == [("Default Ticket", Decimal(25), False)]
    rsvp = posh.parse_tickets(answer({"name": "RSVP", "price": 0, "totalPrice": 0}), "USD")
    assert rsvp[0].is_free and rsvp[0].price == 0
    door = posh.parse_tickets(answer(
        {"name": "$15 General Admission Ticket ", "price": 0, "totalPrice": 0},
        {"name": "Ladies Free Till 11:30 ", "price": 0, "totalPrice": 0},
        {"name": "Comp", "price": 0, "totalPrice": 0, "isHidden": True},
    ), "USD")  # fmt: skip
    expected = [(Decimal(15), True, False), (Decimal(0), False, True)]
    assert [(t.price, t.pay_at_door, t.is_free) for t in door] == expected
    assert posh.parse_tickets({}, "USD") == []
