from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import eventbrite, posh

POSH_LISTED = {
    "id": "6ad6a2f03b1ff88a95e40b53", "title": "END OF THE WORLD - HALLOWEEN EDITION", "url": "end-of-the-world",
    "imageUrl": "https://images.posh.vip/originals/x", "startDate": "2026-10-31T02:00:00.000Z",
    "endDate": "2026-10-31T07:00:00.000Z", "timezone": "America/Toronto", "venueName": "O LOUNGE",
    "minTicketPrice": 29.13, "groupName": "5ONE4.EVENTS", "lineup": [],
}
POSH_PAGE = (
    'self.__next_f.push([1,"{\\"venueAddress\\":\\"1458 Rue Crescent, Montréal, QC H3G 2B6, Canada\\",'
    '\\"location\\":{\\"lat\\":45.4976672,\\"lng\\":-73.5771263},\\"description\\":\\"Biggest party\\",'
    '\\"currency\\":\\"CAD\\",\\"isAttendee\\":false}"])'
)


def test_posh_event_keeps_venue_zone_and_place():
    e = posh.parse_event(POSH_LISTED, posh.rsc_text(POSH_PAGE), "https://posh.vip/e/end-of-the-world")
    assert (e.country, e.city, e.time_zone, e.currency) == ("CA", "Montréal", "America/Toronto", "CAD")
    assert (e.latitude, e.longitude, e.description) == (45.4976672, -73.5771263, "Biggest party")
    s = Settings()
    enriched = Enricher(s.enrichment, s.timezone).enrich(e)
    assert enriched.time_zone == "America/Toronto"  # the source's zone, not the country's guess
    assert {"nightclubs", "holidays"} <= set(enriched.genres)


def test_posh_categories_from_title():
    assert posh.categories("Saturday Night") == ["nightclubs"]
    assert posh.categories("Sunday Brunch Market") == ["shopping", "food"]


def test_eventbrite_categories_and_price():
    listed = {
        "id": "1", "name": "Startup Pitch & Networking", "url": "https://www.eventbrite.com/e/x-1",
        "start_date": "2026-11-21", "start_time": "17:00", "end_date": "2026-11-21", "end_time": "21:00",
        "timezone": "Asia/Jerusalem",
        "primary_venue": {"name": "Hub", "address": {"city": "Tel Aviv-Yafo", "country": "IL", "latitude": "32.09", "longitude": "34.78"}},
        "tags": [{"prefix": "EventbriteCategory", "tag": "EventbriteCategory/101"}, {"prefix": "EventbriteFormat", "tag": "EventbriteFormat/10"}],
    }
    e = eventbrite.parse_event(listed, {"lowPrice": "42.0", "highPrice": "42.0", "priceCurrency": "ILS", "availability": "InStock"})
    assert (e.country, e.min_price, e.max_price, e.source_genres) == (None, 42, None, ["meetups"])
    assert e.start_at.isoformat() == "2026-11-21T17:00:00+02:00"
