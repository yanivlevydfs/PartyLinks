from datetime import date

import pytest
from pydantic import ValidationError

from partylinks.home_rails.config import HomeRail


def test_holiday_row_window_is_its_next_dates() -> None:
    assert HomeRail(key="new_year", holiday="new_year").window(date(2026, 11, 18)) == (
        date(2026, 12, 28),
        date(2027, 1, 1),
    )
    assert HomeRail(key="purim", holiday="purim").window(date(2026, 11, 18)) == (date(2027, 3, 19), date(2027, 3, 24))


def test_month_row_window_is_the_next_such_month() -> None:
    assert HomeRail(key="pride", month=6).window(date(2026, 11, 18)) == (date(2027, 6, 1), date(2027, 6, 30))
    assert HomeRail(key="pride", month=6).window(date(2027, 6, 10)) == (date(2027, 6, 10), date(2027, 6, 30))
    assert HomeRail(key="plain").window(date(2026, 11, 18)) is None


def test_unknown_holiday_is_refused() -> None:
    with pytest.raises(ValidationError):
        HomeRail(key="x", holiday="purrim")


def test_row_cache_refreshes_stale_rows_in_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    from partylinks import home_rails
    from partylinks.home_rails import RowCache

    clock = [1000.0]
    monkeypatch.setattr(home_rails.time, "monotonic", lambda: clock[0])
    built: list[str] = []

    def build(key: str) -> list[str]:
        built.append(key)
        return [f"{key}@{clock[0]:.0f}"]

    cache = RowCache()
    tags = {"a": "1", "b": "1", "c": "1"}
    assert cache.rows(tags, build, ttl=60, per_build=1) == {k: [f"{k}@1000"] for k in tags}  # new rows: all, now
    built.clear()
    clock[0] = 1030
    assert cache.rows(tags, build, ttl=60, per_build=1)["a"] == ["a@1000"] and built == []  # fresh: no query
    clock[0] = 1100
    rows = cache.rows(tags, build, ttl=60, per_build=2)
    assert len(built) == 2 and rows["a"] == ["a@1100"] and rows["c"] == ["c@1000"]  # two refreshed, one kept
    built.clear()
    rows = cache.rows({**tags, "c": "2"}, build, ttl=60, per_build=1)  # c's settings changed: rebuilt at once
    assert built == ["c"] and rows["c"] == ["c@1100"]
    assert list(cache.rows({"b": "1"}, build, ttl=60, per_build=1)) == ["b"]  # a row taken out is gone


def test_row_cache_skips_a_failing_row_and_seeds_stale_rows() -> None:
    from partylinks.home_rails import RowCache

    def build(key: str) -> list[str]:
        if key == "bad":
            raise ValueError(key)
        return [key]

    cache = RowCache()
    cache.seed({"old": ["seeded"], "unknown": ["x"]}, {"old": "1"})
    assert not cache.empty()
    rows = cache.rows({"old": "1", "bad": "1", "new": "1"}, build, ttl=60, per_build=1)
    assert rows == {"old": ["seeded"], "new": ["new"]}  # the new row used the build; the seeded one waits its turn


def test_cache_tag_moves_with_the_day_only_for_dated_rows() -> None:
    day, next_day = date(2026, 11, 26), date(2026, 11, 27)
    assert HomeRail(key="x", genre=["jazz"]).cache_tag(day) == HomeRail(key="x", genre=["jazz"]).cache_tag(next_day)
    assert HomeRail(key="x", today=True).cache_tag(day) != HomeRail(key="x", today=True).cache_tag(next_day)
    assert HomeRail(key="x", genre=["jazz"]).cache_tag(day) != HomeRail(key="x", genre=["rock_indie"]).cache_tag(day)
