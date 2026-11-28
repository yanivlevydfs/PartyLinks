"""A home row's order (home_rails.rows[].sort): "latest" lists the latest to end first ("open latest tonight")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


def test_row_filters_region_day_age_and_place_features(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [
        HomeRail(key="adults", min_age=21),
        HomeRail(key="dogs", places=True, amenity=["כלבים מותרים"]),
        HomeRail(key="dates", places=True, place_tag=["דייט"]),
        HomeRail(key="known", places=True, min_reviews=300),
        HomeRail(key="posh", places=True, min_price_level=3),
        HomeRail(key="cheap", places=True, max_price_level=1),
    ]
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)

    def place(key: str, **extra: object):  # type: ignore[no-untyped-def]
        return event(key, start, url=f"https://example.com/{key}", title=f"Place {key}",
                     source_genres=["attractions"], extra={"happy_hour": True, **extra})  # fmt: skip

    seed(settings, [
        event("club21", start, min_age=21, source_genres=["techno"]),
        event("club18", start, min_age=18, source_genres=["techno"]),
        place("cafe", amenities=["כלבים מותרים"], tags=["בראנץ׳"], reviews=900, price_level=1),
        place("bar", amenities=["ישיבה בחוץ"], tags=["דייט"], reviews=40, price_level=3),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = client.get("/events/rails").json()["rails"]
    ids = {k: [e["id"] for e in v] for k, v in rails.items()}
    assert ids["adults"] == ["test:club21"]
    assert ids["dogs"] == ["test:cafe"] and ids["dates"] == ["test:bar"]
    assert ids["known"] == ["test:cafe"] and ids["posh"] == ["test:bar"] and ids["cheap"] == ["test:cafe"]


def test_region_and_day_of_week_reach_the_event_filters() -> None:
    from zoneinfo import ZoneInfo

    from partylinks.api import _home_row_filters
    from partylinks.config import Settings

    asked: dict[str, object] = {}

    def filters_of(**kw: object) -> list:  # type: ignore[type-arg]
        asked.update(kw)
        return []

    row = HomeRail(key="north_thu", region=["haifa", "galilee"], day_of_week=["thursday"])
    _home_row_filters(filters_of, Settings(), row, datetime(2026, 11, 28, 20, tzinfo=ZoneInfo("Asia/Jerusalem")))
    assert asked["region"] == ["haifa", "galilee"] and asked["day_of_week"] == ["thursday"]


def test_compact_rails_send_each_event_once_and_keep_the_pins_per_row(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.home_rails.rows = [HomeRail(key="a", genre=["techno"]), HomeRail(key="b", genre=["techno"])]
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    seed(settings, [event(f"t{i}", start + timedelta(hours=i), source_genres=["techno"]) for i in range(3)])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        full = client.get("/events/rails").json()
        compact = client.get("/events/rails?compact=true").json()
    assert set(compact) >= {"events", "rails", "pinned"}
    assert compact["rails"]["a"] == [c["id"] for c in full["rails"]["a"]] == compact["rails"]["b"]
    assert sorted(compact["events"]) == ["test:t0", "test:t1", "test:t2"]  # once, though in two rows
    assert compact["events"]["test:t1"]["title"] == full["rails"]["a"][1]["title"]
    assert compact["pinned"] == {}


def test_kept_rails_seed_from_the_compact_copy() -> None:
    import json

    from partylinks.api import _kept_rails

    class Cache:
        def body(self, key: tuple[str, str]) -> bytes | None:
            if key[1] != "compact=true":
                return None
            return json.dumps({
                "events": {"x": {"id": "x"}, "y": {"id": "y"}},
                "rails": {"r": ["x", "y"], "s": ["y"]},
                "pinned": {"r": ["y"]},
            }).encode()  # fmt: skip

    assert _kept_rails(Cache(), "/events/rails") == {"r": [{"id": "x"}], "s": [{"id": "y"}]}  # type: ignore[arg-type]


@pytest.mark.skipif(not (Path(__file__).parents[1] / "web/dist/index.html").is_file(), reason="needs the web build")
def test_the_home_page_preloads_its_rows_and_other_pages_do_not(tmp_path: Path) -> None:
    from partylinks.seo import HOME_RAILS_URL

    assert HOME_RAILS_URL == "/events/rails?compact=true"  # what web/src/api.ts useRails fetches
    preload = f'<link rel="preload" href="{HOME_RAILS_URL}" as="fetch" crossorigin="anonymous" />'
    with TestClient(create_app(settings_for(tmp_path), start_scheduler=False)) as client:
        assert preload in client.get("/").text
        assert preload not in client.get("/privacy").text
