from datetime import datetime
from decimal import Decimal

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import TierStatus
from partylinks.parsing import zone
from partylinks.scrapers.klika import KlikaSettings, parse_event

TZ = "Asia/Jerusalem"
S = KlikaSettings(cron="53 11,23 * * *")

ROW = {
    "id": "355919d5-c8d4-46d6-a047-efc468950803",
    "slug": "heshek-vol1",
    "name": "חשק  Vol1 ",
    "event_date": "2026-11-12T00:00:00+00:00",
    "event_start": "23:00:00",
    "event_end": "06:00:00",
    "doors_open": "23:00:00",
    "venue_name": "4TRESS",
    "venue_address": "משה מאור 3 תל אביב",
    "description": "האוס, טכנו ואסתטיקה אפלה.",
    "ecosystem_id": "e0303781",
    "registration_only": False,
    "members_only": False,
}
PAGE = {
    "hero_image": "https://gypklkntkzgvjtqffdvb.supabase.co/storage/v1/object/public/event-graphics/x/sales/1.webp",
    "tiers": [
        {"id": "a", "name": "חשק ברור", "price": 90, "state": "sold_out"},
        {"id": "b", "name": "חשק פלוס", "price": 120, "state": "available"},
        {"id": "c", "name": "חשק ספונטני", "price": 150, "state": "pending"},
    ],
    "lineup": [
        {"dj_name": "Vika Shwarz", "play_time": "23:00:00", "genre": None},
        {"dj_name": "Ari Miller", "b2b_partner_name": "Kobi Saka", "play_time": "03:00:00", "genre": None},
        {"dj_name": "Sahar Z", "play_time": "01:00:00", "genre": None},
    ],
}
LINE = {"id": "e0303781", "name": "חשק - 4Tress", "genres": ["האוס", "טכנו"]}


def test_parse_event():
    e = parse_event(ROW, PAGE, LINE, S, TZ)
    assert e.title == "חשק  Vol1"
    assert e.start_at == datetime(2026, 11, 12, 23, 0, tzinfo=zone(TZ))
    assert e.end_at == datetime(2026, 11, 13, 6, 0, tzinfo=zone(TZ))  # after midnight
    assert str(e.url) == "https://klika.live/t/heshek-vol1"
    assert e.extra["purchase_url"] == "https://klika.live/t/heshek-vol1"
    assert (e.venue_name, e.address) == ("4TRESS", "משה מאור 3 תל אביב")
    assert e.source_genres == ["האוס", "טכנו"]
    assert e.artists == ["Vika Shwarz", "Ari Miller", "Kobi Saka", "Sahar Z"]
    assert e.organizer == "חשק - 4Tress"
    assert [t.status for t in e.tickets] == [TierStatus.SOLD_OUT, TierStatus.ON_SALE, TierStatus.COMING_SOON]
    assert e.images == [PAGE["hero_image"]]
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(e)
    assert {"house", "techno"} <= set(enriched.genres)


def test_no_clock_and_registration_only():
    row = ROW | {"event_start": None, "doors_open": None, "event_end": None, "registration_only": True}
    e = parse_event(row, {}, {}, S, TZ)
    assert not e.time_known and e.end_at is None
    assert e.tickets[0].is_free and e.tickets[0].price == Decimal(0)


def test_missing_slug_or_date():
    assert parse_event(ROW | {"slug": None}, PAGE, LINE, S, TZ) is None
    assert parse_event(ROW | {"event_date": None}, PAGE, LINE, S, TZ) is None
