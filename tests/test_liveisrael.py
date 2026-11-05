import json
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.liveisrael import LiveIsraelSettings, parse_event, split_place

TZ = "Asia/Jerusalem"
S = LiveIsraelSettings(cron="0 0 * * *")
DATA = json.loads((Path(__file__).parent / "fixtures" / "liveisrael" / "events.json").read_text(encoding="utf-8"))
TAGS = {t["id"]: t["name"] for t in DATA["tags"]}
ROWS = {r["link"].rstrip("/").rsplit("/", 1)[-1]: r for r in DATA["rows"]}
SETTINGS = Settings()
ENRICHER = Enricher(SETTINGS.enrichment, SETTINGS.timezone)


def test_club_night_with_address_and_free_signup_link() -> None:
    event = parse_event(ROWS["friday-rabbit-tel-aviv"], TAGS, S, TZ)
    assert event is not None
    assert event.title.startswith("Rabbit | ראביט") and event.source_event_id == str(
        ROWS["friday-rabbit-tel-aviv"]["id"]
    )
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-11-06T21:00:00+02:00" and event.time_known
    assert (event.venue_name, event.address) == (None, "שדרות רוטשילד 14, תל אביב-יפו")
    assert event.extra["purchase_url"] == "https://bit.ly/3UtkarA"
    assert event.images == ["https://liveisrael.co/wp-content/uploads/2026/11/x.webp"]
    enriched = ENRICHER.enrich(event)
    assert enriched is not None and enriched.genres


def test_venue_after_midnight_age_and_lineup() -> None:
    event = parse_event(ROWS["atlanta-thursdays-tel-aviv"], TAGS, S, TZ)
    assert event is not None
    assert event.venue_name == "אטלנטה" and event.address == "מועדון אטלנטה, שדרות שאול המלך 2, תל אביב-יפו"
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-11-05T23:30:00+02:00"
    assert event.min_age == 18


def test_secret_nature_party_city_from_tags_and_lineup_field() -> None:
    event = parse_event(ROWS["okavango-delta-nature-party"], TAGS, S, TZ)
    assert event is not None
    assert (event.venue_name, event.address) == (None, None)
    assert event.artists[:2] == ["OSHI", "ORIGAMI"] and len(event.artists) == 8
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-11-12T23:59:00+02:00"
    assert "טראנס" in event.source_genres and "18+" not in event.source_genres
    enriched = ENRICHER.enrich(event)
    assert enriched is not None and "trance" in enriched.genres


def test_split_place() -> None:
    assert split_place("מועדון שלוותה, התערוכה 3, נמל תל אביב", S) == (
        "שלוותה",
        "מועדון שלוותה, התערוכה 3, נמל תל אביב",
    )
    assert split_place("מרכז הארץ (המיקום המדויק יימסר לרוכשים)", S) == (None, None)
    assert split_place("נמל קיסריה.", S) == (None, "נמל קיסריה")
    after_midnight = parse_event(
        ROWS["atlanta-thursdays-tel-aviv"]
        | {"content": {"rendered": "<ul><li><strong>שעת פתיחת דלתות:</strong> 00:30</li></ul>"}},
        TAGS,
        S,
        TZ,
    )
    assert after_midnight is not None and after_midnight.start_at is not None
    assert after_midnight.start_at.isoformat() == "2026-11-06T00:30:00+02:00"
