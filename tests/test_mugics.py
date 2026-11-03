from decimal import Decimal

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.mugics import MugicsSettings, parse_event

TZ = "Asia/Jerusalem"
S = MugicsSettings(cron="0 0 * * *")
ROW = {
    "_id": "6ae3094c4f357529014d85bf",
    "type": "liveMusic",
    "name": "רותם בר אור – מופע סולו ב- 360°",
    "desc": "מארח את תהל לוי. מופע אינטימי וחשוף.",
    "date": "2026-11-06T22:00:00.000Z",
    "time": 73800,
    "category": "intimatespace",
    "subCategories": ["אינדי", "ישראלית"],
    "location": {
        "position": {"lat": 32.4719805, "lng": 34.9814337},
        "street": "נבו",
        "number": None,
        "city": "פרדס חנה",
        "country": "ישראל",
        "revenueName": "היוניקורן",
    },
    "prices": {"currency": "ILS", "min": 0, "max": 0, "price": 95, "priceType": "price"},
    "images": [
        {"src": "https://media.mugics.com/pins/W97Wpvqyo/W97Wpvqyo.jpg"},
        {"src": "https://media.mugics.com/pins/FOXrtt2X8/FOXrtt2X8.jpg"},
    ],
    "ticketLink": "https://theunicorn.co.il/event/rotem-bar-or/",
}


def test_pin_parses_to_a_mapped_live_show() -> None:
    event = parse_event(ROW, S, TZ)
    assert event is not None
    assert str(event.url).startswith("https://mugics.com/he/pin/6ae3094c4f357529014d85bf/")
    assert event.title == "רותם בר אור – מופע סולו ב- 360°"
    # date is the local midnight in UTC (Nov 7, 00:00 Israel), time the seconds after it (20:30)
    assert event.start_at and event.start_at.isoformat() == "2026-11-07T20:30:00+02:00" and event.time_known
    assert (event.venue_name, event.city, event.address) == ("היוניקורן", "פרדס חנה", "נבו, פרדס חנה")
    assert (event.latitude, event.longitude) == (32.4719805, 34.9814337)
    assert event.source_genres == ["הופעה חיה", "הופעות אינטימיות", "אינדי", "ישראלית"]
    assert event.extra["purchase_url"] == "https://theunicorn.co.il/event/rotem-bar-or/"
    assert event.min_price == event.max_price == Decimal(95) and len(event.images) == 2
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and {"live_show", "israeli", "rock_indie"} <= set(enriched.genres)


def test_free_range_social_link_city_venue_and_skips() -> None:
    free = parse_event(ROW | {"prices": {"priceType": "free"}, "ticketLink": "https://www.facebook.com/x"}, S, TZ)
    assert free is not None and free.min_price == 0 and free.tickets[0].is_free
    assert free.extra["purchase_url"] is None
    ranged = parse_event(ROW | {"prices": {"priceType": "price-range", "min": 60, "max": 120}}, S, TZ)
    assert ranged is not None and (ranged.min_price, ranged.max_price) == (Decimal(60), Decimal(120))
    town = parse_event(ROW | {"location": ROW["location"] | {"revenueName": "פרדס-חנה"}}, S, TZ)
    assert town is not None and town.venue_name is None
    no_time = parse_event(ROW | {"time": None}, S, TZ)
    assert no_time is not None and not no_time.time_known and no_time.start_at.hour == 0
    assert parse_event(ROW | {"type": "band"}, S, TZ) is None
    assert parse_event(ROW | {"date": None}, S, TZ) is None
    assert parse_event(ROW | {"location": ROW["location"] | {"country": "Greece"}}, S, TZ) is None


def test_venue_spellings_and_town_spelled_otherwise() -> None:
    hall = parse_event(ROW | {"location": ROW["location"] | {"revenueName": "צוללת צהובה"}}, S, TZ)
    assert hall is not None and hall.venue_name == "הצוללת הצהובה"
    tivon = ROW["location"] | {"city": "קריית טבעון", "revenueName": "קרית טבעון"}
    town = parse_event(ROW | {"location": tivon}, S, TZ)
    assert town is not None and town.venue_name is None
