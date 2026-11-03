import json
from decimal import Decimal

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.plusparty import PlusPartySettings, listing_rows, parse_event

TZ = "Asia/Jerusalem"
S = PlusPartySettings(cron="0 0 * * *")
ROW = {
    "id": "c376f12a-2e5a-4164-a717-7f1eba728ee6",
    "slug": "thursday-moon-mainstream-moonchild-tlv-05-11",
    "title": "THURSDAY MOON – מיינסטרים והיפ הופ במונצ׳יילד נמל תל אביב – 5.11",
    "description": "ליין המיינסטרים של ימי חמישי במונצ׳יילד בנמל תל אביב. כרטיסים ב-Zygo.",
    "region": "merkaz",
    "kind": "party",
    "event_date": "2026-11-05T20:30:00+00:00",
    "end_at": "2026-11-06T03:30:00+00:00",
    "location": "מונצ׳יילד, נמל תל אביב",
    "image_url": "https://cdysewodfwwobjeennns.supabase.co/storage/v1/object/sign/media/events/7fc069f4.jpg?token=x",
    "og_image": None,
    "external_link": None,
    "price": None,
    "price_min": 0,
    "price_max": 87,
    "currency": "ILS",
    "venue_name": "Moonchild – נמל תל אביב",
    "street_address": "האנגר 23, נמל תל אביב",
    "city": "תל אביב",
    "latitude": 32.096203,
    "longitude": 34.773158,
    "organizer_name": "Waves",
    "performers": ["Danino", "Ron Levy", "Arivva", "Max David"],
    "ticket_status": "in_stock",
    "event_status": "scheduled",
    "age_limit": 18,
    "published": True,
}


def _page(rows: list[dict]) -> str:
    state = json.dumps({"mutations": [], "queries": [{"queryKey": ["events", {}], "state": {"data": rows}}]})
    return f"<script>$R={{dehydratedData:$R[1]={{queryClientState:{json.dumps(state)}}}}}</script>"


def test_listing_state_row_parses_to_a_mapped_party() -> None:
    rows = listing_rows(_page([ROW]), S)
    assert rows == [ROW]
    event = parse_event(rows[0], S, TZ)
    assert event is not None
    assert str(event.url) == "https://plusparty.co.il/events/thursday-moon-mainstream-moonchild-tlv-05-11"
    assert event.source_event_id == ROW["id"] and event.title == ROW["title"]
    assert event.start_at.isoformat() == "2026-11-05T22:30:00+02:00" and event.time_known
    assert event.end_at and event.end_at.isoformat() == "2026-11-06T05:30:00+02:00"
    assert (event.venue_name, event.address, event.city) == (
        "Moonchild – נמל תל אביב",
        "האנגר 23, נמל תל אביב",  # the street line already names the city
        "תל אביב",
    )
    assert (event.latitude, event.longitude) == (32.096203, 34.773158)
    assert event.source_genres == ["מסיבה"] and event.min_age == 18 and event.organizer == "Waves"
    assert event.artists == ["Danino", "Ron Levy", "Arivva", "Max David"]
    assert (event.min_price, event.max_price) == (Decimal(0), Decimal(87)) and len(event.images) == 1
    assert "purchase_url" not in event.extra  # the seller link sits behind the site's sign-up
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and {"nightclubs", "mainstream", "hip_hop"} <= set(enriched.genres)


def test_show_kind_gets_a_live_show_genre() -> None:
    event = parse_event(ROW | {"kind": "show", "title": "עוזי נבון והחוויות בבארבי", "description": None}, S, TZ)
    assert event is not None and event.source_genres == ["הופעה"]
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and "live_show" in enriched.genres


def test_secret_place_town_venue_no_city_and_skips() -> None:
    secret = ROW | {
        "venue_name": "לוקיישן בטבע",
        "street_address": None,
        "city": None,
        "location": "לוקיישן בטבע (יפורסם לרוכשים)",
        "latitude": None,
        "longitude": None,
    }
    event = parse_event(secret, S, TZ)
    assert event is not None and (event.venue_name, event.address, event.city) == (None, None, None)
    town = parse_event(ROW | {"venue_name": "כפר שיטים", "city": "כפר שיטים", "street_address": None}, S, TZ)
    assert town is not None and town.venue_name is None
    area = ROW | {"venue_name": "עמק יזרעאל", "city": None, "street_address": None, "location": "עמק יזרעאל"}
    assert parse_event(area, S, TZ).venue_name is None
    no_city = ROW | {
        "venue_name": None,
        "city": None,
        "street_address": "אלטלנה 8",
        "location": "אלטלנה 8, ראשון לציון",
    }
    assert parse_event(no_city, S, TZ).address == "אלטלנה 8, ראשון לציון"
    hall = ROW | {"venue_name": "מרכז הקונגרסים חיפה", "city": "חיפה", "location": "מרכז הקונגרסים חיפה"}
    assert parse_event(hall, S, TZ).venue_name == "מרכז הקונגרסים חיפה"
    sold = parse_event(ROW | {"ticket_status": "sold_out", "end_at": None}, S, TZ)
    assert sold is not None and sold.is_sold_out and sold.end_at is None
    assert parse_event(ROW | {"event_date": None}, S, TZ) is None
    assert parse_event(ROW | {"slug": None}, S, TZ) is None
    assert parse_event(ROW | {"event_status": "cancelled"}, S, TZ) is None
    with pytest.raises(ParseError):
        listing_rows("<html></html>", S)
