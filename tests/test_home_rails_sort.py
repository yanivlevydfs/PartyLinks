"""A home row's order (home_rails.rows[].sort): "latest" lists the latest to end first ("open latest tonight")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.home_rails.config import HomeRail
from tests.test_storage_api import event, seed, settings_for


def test_latest_lists_the_latest_end_first_and_trending_stays_the_default(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [
        HomeRail(key="late", genre=["techno"], sort="latest"),
        HomeRail(key="usual", genre=["techno"]),
    ]
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    seed(settings, [
        event("early", start, end_at=start + timedelta(hours=2), source_genres=["techno"]),
        event("dawn", start + timedelta(hours=1), end_at=start + timedelta(hours=9), source_genres=["techno"]),
        # No end time: the start plus retention.assumed_duration_hours (3 h): between the other two.
        event("open", start + timedelta(hours=3), end_at=None, source_genres=["techno"]),
    ])  # fmt: skip
    assert HomeRail(key="x").sort == "trending"
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = client.get("/events/rails").json()["rails"]
    assert [e["id"] for e in rails["late"]] == ["test:dawn", "test:open", "test:early"]
    assert [e["id"] for e in rails["usual"]] == ["test:early", "test:dawn", "test:open"]  # by start, no other sites


def test_places_rows_by_closing_hour_and_by_rating(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [
        HomeRail(key="late_places", places=True, sort="latest"),
        HomeRail(key="best_places", places=True, sort="rating"),
        HomeRail(key="events_only", places=False),
    ]
    opens = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    place = {"source_genres": ["attractions"], "url": None}

    def bar(key: str, closes: int, rating: float, reviews: int = 400):  # type: ignore[no-untyped-def]
        extra = {"happy_hour": True, "rating": rating, "reviews": reviews}
        fields = place | {"url": f"https://example.com/{key}", "extra": extra}
        return event(key, opens, end_at=opens + timedelta(hours=closes), title=f"Place {key}", **fields)

    seed(settings, [
        bar("billiards", 16, 4.1), bar("cafe", 8, 4.9), bar("club", 12, 4.5), bar("park", 6, 5.0, reviews=2),
        event("party", opens, end_at=opens + timedelta(hours=20), source_genres=["techno"]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = client.get("/events/rails").json()["rails"]
    assert [e["id"] for e in rails["late_places"]] == ["test:billiards", "test:club", "test:cafe", "test:park"]
    # A 5.0 of two reviews is not the best place: the rating is weighted by its reviews.
    assert [e["id"] for e in rails["best_places"]] == ["test:cafe", "test:club", "test:park", "test:billiards"]
    assert [e["id"] for e in rails["events_only"]] == ["test:party"]


def test_open_latest_after_midnight_is_still_last_night() -> None:
    from datetime import date
    from zoneinfo import ZoneInfo

    from partylinks.api import _home_row_filters
    from partylinks.config import Settings

    asked: dict[str, object] = {}

    def filters_of(**kw: object) -> list:  # type: ignore[type-arg]
        asked.update(kw)
        return []

    il = ZoneInfo("Asia/Jerusalem")
    late = HomeRail(key="latest_tonight", today=True, places=True, sort="latest")
    _home_row_filters(filters_of, Settings(), late, datetime(2026, 11, 28, 1, 40, tzinfo=il))
    assert asked["on_date"] == date(2026, 11, 27)  # 01:40: the places open since last night
    _home_row_filters(filters_of, Settings(), late, datetime(2026, 11, 28, 18, 0, tzinfo=il))
    assert asked["on_date"] == date(2026, 11, 28)
    _home_row_filters(
        filters_of, Settings(), HomeRail(key="tonight", today=True), datetime(2026, 11, 28, 1, 40, tzinfo=il)
    )
    assert asked["on_date"] == date(2026, 11, 28)  # other "today" rows keep the calendar day
