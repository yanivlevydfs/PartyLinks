from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.secrettelavivapp import Lookups, SecretTelAvivAppSettings, parse_event

TZ = "Asia/Jerusalem"
S = SecretTelAvivAppSettings(cron="0 0 * * *")
LOOKUPS = Lookups(
    locations={"loc1": {"restaurant_name": "Matmon", "location_address_hebrew": "דרך יפו 9, תל אביב"}},
    categories={"c1": "Screening", "c2": "Culture Highlights"},
)
ROW = {
    "id": "6ae478d1a907c760e7d56514",
    "slug": "perfect-blue",
    "event_name": "Perfect Blue",
    "event_name_hebrew": "Perfect Blue",
    "description_en": "Screening and lecture.",
    "description_he": "הקרנה והרצאה.",
    "event_start_date": "2026-11-29",
    "event_end_date": "2026-11-29",
    "event_start_time": "21:00",
    "event_end_time": "23:00",
    "event_link": "https://matmon.space/perfect-blue/",
    "event_categories": ["c1", "c2", "unknown"],
    "image_url": "https://www.secrettelaviv.com/wp-content/uploads/2026/10/image-580.jpg",
    "base44_location_id": "loc1",
    "event_lat": 32.0608,
    "event_lng": 34.7694,
}


def test_row_parses_to_a_mapped_event_on_the_map() -> None:
    event = parse_event(ROW, LOOKUPS, S, TZ)
    assert event is not None
    assert str(event.url) == "https://secrettelaviv.app/event/perfect-blue"
    assert event.title == "Perfect Blue" and event.description == "הקרנה והרצאה."
    assert event.start_at and event.start_at.hour == 21 and event.end_at and event.end_at.hour == 23
    assert (event.venue_name, event.latitude, event.longitude) == ("Matmon", 32.0608, 34.7694)
    assert event.source_genres == ["screening", "culture highlights"]
    assert event.extra["purchase_url"] == "https://matmon.space/perfect-blue/"
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and {"cinema", "culture"} <= set(enriched.genres)


def test_placeholder_end_social_link_and_missing_slug() -> None:
    row = ROW | {"event_end_time": "23:55", "event_link": "https://www.instagram.com/matmon/"}
    event = parse_event(row, LOOKUPS, S, TZ)
    assert event is not None and event.end_at is None and event.extra["purchase_url"] is None
    assert parse_event(ROW | {"slug": None}, LOOKUPS, S, TZ) is None


def test_price_and_free_entry_from_the_descriptions() -> None:
    # Real descriptions (November 2026): the Hebrew one writes the price, the English one repeats it.
    row = ROW | {
        "description_he": "ראשון| 29.11 | 21:00 | כרטיס: 40 ₪\n\nמספר המקומות מוגבל | 18+",
        "description_en": "21:00. Tickets: 40 ₪. Limited spots available. 18+.",
    }
    event = parse_event(row, LOOKUPS, S, TZ)
    assert event is not None and [(t.name, t.price) for t in event.tickets] == [("כרטיס", 40)]
    row = ROW | {
        "description_he": "רביעי | 4.11 | 20:00 דלתות | כניסה חופשית | רבי עקיבא 3, כרם התימנים",
        "description_en": "Doors at 20:00, entry is free.",
    }
    event = parse_event(row, LOOKUPS, S, TZ)
    assert event is not None and [(t.price, t.is_free) for t in event.tickets] == [(0, True)]
    plain = parse_event(ROW, LOOKUPS, S, TZ)
    assert plain is not None and plain.tickets == []
