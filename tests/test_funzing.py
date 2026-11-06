from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.funzing import FunzingScraper, FunzingSettings, parse_funz, parse_interests

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


def test_package_priced_experience_reads_starting_price() -> None:
    # Funz 38332 (couple painting workshop, Caesarea): adult and kids price "₪0", the site's "from" price 650.
    funz = {**FUNZ, "id": 38332, "adult_price": "₪0", "kids_price": "₪0", "starting_price": "₪650"}
    first = parse_funz(funz, {})[0]
    assert (first.min_price, first.max_price) == (650, 650)
    assert parse_funz({**funz, "starting_price": None}, {})[0].min_price is None


def test_all_dates_read_when_page_holds_fewer_within_horizon() -> None:
    # The page gives the first 10 dates; /funz/<id>/events gives all. Funz 1037 has 20, the page 10.
    now = datetime.now().astimezone()

    def ev(i: int, days: int) -> dict[str, Any]:
        return {"id": i, "date_start": (now + timedelta(days=days)).isoformat(), "end_time": "23:00"}

    api = "https://api-il.funzing.com/api/v2"
    bodies: dict[str, Any] = {
        f"{api}/categories": {},
        f"{api}/funz/1037": {"funz": {**FUNZ, "id": 1037, "events": [ev(1, 5)]}},
        f"{api}/funz/1037/events": {"events": [ev(0, -1), ev(1, 5), ev(2, 10), ev(3, 200)]},
        f"{api}/funz/2": {"funz": {**FUNZ, "id": 2, "events": [ev(9, 3)]}},
    }
    listing = [{"id": 1037, "number_of_upcoming_dates": 3}, {"id": 2, "number_of_upcoming_dates": 1}, {"id": 3}]
    asked: list[str] = []

    async def fake_get_json(url: str, *, params: dict[str, Any] | None = None, **_: object) -> Any:
        asked.append(url)
        if url.endswith("search.json"):
            return {"funzes": listing if params and params["page"] == 1 else []}
        return bodies[url]

    scraper = FunzingScraper(FunzingSettings(cron="0 4 * * *", days_ahead=120), HttpSettings(), "Asia/Jerusalem")
    scraper.get_json = fake_get_json  # type: ignore[method-assign]

    async def collect() -> list[str]:
        return [e.source_event_id async for e in scraper.scrape()]

    assert sorted(asyncio.run(collect())) == ["1", "2", "9"]  # not the past one, not 200 days ahead
    assert f"{api}/funz/2/events" not in asked  # the page held every date: no extra request
