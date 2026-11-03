from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.efokulam import EfoKulamSettings, parse_event, split_address

TZ = "Asia/Jerusalem"
S = EfoKulamSettings(cron="0 0 * * *")
TYPES = {43: "טכנו", 42: "מיינסטרים"}
ROW = {
    "id": 11173,
    "link": "https://efokulam.co.il/events/wednesday-john-doe-5/",
    "title": {"rendered": "Copy of Wednesday &#8211; John Doe"},
    "content": {"rendered": "<p>long text</p>"},
    "meta": {
        "event-date": "1793833200",  # 2026-11-04 23:00 written as UTC: the local wall clock
        "event-gate_open": "23:00",
        "address": "שלוותה, התערוכה, תל אביב-יפו, ישראל",
        "ticket_link": "https://go-out.co/event/1793550545645",
        "short_description": "<p>ערב <b>טכנו</b> ברביעי.</p>",
    },
    "event-type": [43, 42],
    "_embedded": {"wp:featuredmedia": [{"source_url": "https://efokulam.co.il/wp-content/uploads/2026/11/e.webp"}]},
}


def test_row_parses_to_a_club_night() -> None:
    event = parse_event(ROW, TYPES, S, TZ)
    assert event is not None
    assert event.source_event_id == "11173" and str(event.url) == ROW["link"]
    assert event.title == "Wednesday – John Doe"
    assert event.start_at.isoformat() == "2026-11-04T23:00:00+02:00" and event.time_known
    assert event.end_at is None
    assert (event.venue_name, event.address, event.country) == ("שלוותה", "שלוותה, התערוכה, תל אביב-יפו, ישראל", None)
    assert event.source_genres == ["טכנו", "מיינסטרים"]
    assert event.description == "ערב טכנו ברביעי."
    assert event.extra["purchase_url"] == "https://go-out.co/event/1793550545645"
    assert event.images == ["https://efokulam.co.il/wp-content/uploads/2026/11/e.webp"]
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and enriched.city == "תל אביב" and {"techno", "mainstream"} <= set(enriched.genres)


def test_no_category_gets_a_party_genre_and_skips() -> None:
    bare = parse_event(ROW | {"event-type": []}, TYPES, S, TZ)
    assert bare is not None and bare.source_genres == ["מסיבה"]
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(bare)
    assert enriched is not None and "nightclubs" in enriched.genres
    assert parse_event(ROW | {"meta": ROW["meta"] | {"event-date": ""}}, TYPES, S, TZ) is None
    assert parse_event(ROW | {"title": {"rendered": ""}}, TYPES, S, TZ) is None


def test_address_venue_city_and_abroad() -> None:
    # A street, a street + number, a city alone: no venue.
    assert split_address("הלוחמים, תל אביב-יפו, ישראל", S)[0] is None
    assert split_address("ת.ד. 6371, האנגר 23, נמל תל אביב, תל אביב-יפו", S)[:2] == (
        None,
        "האנגר 23, נמל תל אביב, תל אביב-יפו",
    )
    assert split_address("דרך חברון, תלפיות, ירושלים", S)[0] is None
    assert split_address("תל אביב, יפו, תל אביב-יפו", S)[0] is None
    assert split_address("בר גיורא | תל אביב, שלמה אבן גבירול, תל אביב-יפו, ישראל", S)[0] == "בר גיורא"
    abroad = parse_event(
        ROW | {"meta": ROW["meta"] | {"address": '"PR BEACH" גולדן סנדס, וארנה, בולגריה', "event-date": "1809039600",
                                      "event-gate_open": "22:00"}},
        TYPES,
        S,
        TZ,
    )  # fmt: skip
    assert abroad is not None and abroad.country == "BG" and abroad.time_zone == "Europe/Sofia"
    assert abroad.start_at.isoformat() == "2027-04-29T22:00:00+03:00"
