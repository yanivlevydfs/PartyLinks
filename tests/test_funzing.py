from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.funzing import parse_funz, parse_interests

CATEGORIES = {"category_boxes": [{"slug": "tours", "interests": [{"key": "history", "display_name": "היסטוריה"}]}]}

FUNZ = {
    "id": 22361, "title": "יפו שמאחורי הקלעים", "description": "<p>סיור ביפו הנסתרת</p>",
    "what_you_get": "<ul><li>ביקור בשוק היווני</li></ul>", "category_slug": "tours", "mobile_display_name": "סיורים",
    "funz_interests": "evening_tour, history", "adult_price": "₪100", "kids_price": "₪0",
    "city": "תל אביב-יפו", "displayed_address": "תל אביב-יפו", "latitude": 32.054967, "longitude": 34.756352,
    "cover_url": "https://res.cloudinary.com/dnn7h5tec/image/upload/w_370,h_270,q_auto,c_fill/prod/funz/22361/a.jpg",
    "canonical_url": "https://il.funzing.com/funz/%D7%99%D7%A4%D7%95-22361",
    "owner": {"id": 172750, "first_name": "דודי"},
    "events": [
        {"id": 916968, "sold_out": False, "date_start": "2026-10-29T18:00:00.000+02:00", "end_time": "20:00"},
        {"id": 916969, "sold_out": True, "date_start": "2026-11-05T23:00:00.000+02:00", "end_time": "01:00"},
    ],
}  # fmt: skip


def test_experience_per_date() -> None:
    interests = parse_interests(CATEGORIES)
    assert interests == {"history": "היסטוריה"}
    first, second = parse_funz(FUNZ, interests)
    assert first.source_event_id == "916968" and str(first.url) == FUNZ["canonical_url"]
    assert first.start_at is not None and first.start_at.isoformat() == "2026-10-29T18:00:00+02:00"
    assert first.end_at is not None and first.end_at.isoformat() == "2026-10-29T20:00:00+02:00"
    assert second.end_at is not None and second.end_at.isoformat() == "2026-11-06T01:00:00+02:00"
    assert (first.is_sold_out, second.is_sold_out) == (False, True)
    assert (first.min_price, first.max_price) == (100, 100)
    assert (first.city, first.address, first.latitude) == ("תל אביב-יפו", None, 32.054967)
    assert first.images == ["https://res.cloudinary.com/dnn7h5tec/image/upload/prod/funz/22361/a.jpg"]
    assert first.organizer == "דודי" and "בשוק היווני" in (first.description or "")
    assert "tours" in Enricher(Settings().enrichment, "Asia/Jerusalem")._detect_genres(first)
    assert parse_funz({"id": 1}, {}) == []
    assert parse_funz({**FUNZ, "city": "אונליין"}, interests) == []
