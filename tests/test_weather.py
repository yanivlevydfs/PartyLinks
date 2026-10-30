"""Weather: MET slots parsed, one fetch per place per hour (kept across restarts), a global fetch cap."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

from partylinks import weather
from partylinks.config import Settings, WeatherSettings

MET = {
    "properties": {
        "timeseries": [
            {"time": "2026-10-26T18:00:00Z", "data": {"instant": {"details": {"air_temperature": 22.4}},
             "next_1_hours": {"summary": {"symbol_code": "clearsky_night"}, "details": {"precipitation_amount": 0}}}},
            {"time": "2026-10-29T00:00:00Z", "data": {"instant": {"details": {"air_temperature": 17.0}},
             "next_6_hours": {"summary": {"symbol_code": "rainshowers_night"}, "details": {"precipitation_amount": 2.1}}}},
            {"time": "2026-11-05T00:00:00Z", "data": {"instant": {"details": {"air_temperature": 15.0}}}},
        ]
    }
}  # fmt: skip


class FakeResponse:
    headers: ClassVar[dict[str, str]] = {"expires": "Sun, 25 Oct 2026 00:00:00 GMT"}  # in the past: the 1 h minimum applies

    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return MET


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...
        async def __aenter__(self) -> Self:
            return self
        async def __aexit__(self, *_: object) -> None: ...
        async def get(self, url: str, params: dict[str, Any]) -> FakeResponse:
            made.append(params)
            return FakeResponse()

    monkeypatch.setattr(weather.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(weather, "_cache", {})
    monkeypatch.setattr(weather, "_fetched_at", [])
    monkeypatch.setattr(weather, "_loaded_from", None)
    return made


def test_parse_forecast() -> None:
    assert weather.parse_forecast(MET) == [
        ["2026-10-26T18:00:00Z", "clearsky_night", 22.4, 0, 1],
        ["2026-10-29T00:00:00Z", "rainshowers_night", 17.0, 2.1, 6],
    ]


def test_one_fetch_per_place_kept_across_restarts(tmp_path: Path, calls: list[dict[str, Any]]) -> None:
    path = tmp_path / "weather_cache.json"
    first = asyncio.run(weather.forecast(32.0853, 34.7818, "test", path))
    asyncio.run(weather.forecast(32.0612, 34.7701, "test", path))  # same 0.1° cell: from the cache
    assert len(calls) == 1 and calls[0] == {"lat": 32.1, "lon": 34.8} and len(first) == 2

    weather._cache.clear()  # a restart: the file brings it back
    weather._loaded_from = None
    assert asyncio.run(weather.forecast(32.08, 34.78, "test", path)) == first and len(calls) == 1


def test_fetches_are_capped(tmp_path: Path, calls: list[dict[str, Any]]) -> None:
    cfg = WeatherSettings(max_fetches=2)
    for lat in (31.0, 32.0, 33.0):
        asyncio.run(weather.forecast(lat, 35.0, "test", tmp_path / "w.json", cfg))
    assert len(calls) == 2


def test_a_failed_place_lookup_backs_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nominatim down: one timeout, then the next unknown places answer at once (no request each)."""
    tried: list[str] = []

    class DownClient:
        def __init__(self, **_: Any) -> None: ...
        async def __aenter__(self) -> Self:
            return self
        async def __aexit__(self, *_: object) -> None: ...
        async def get(self, url: str, params: dict[str, Any]) -> Any:
            tried.append(params["q"])
            raise weather.httpx.ConnectTimeout("down")

    monkeypatch.setattr(weather.httpx, "AsyncClient", DownClient)
    monkeypatch.setattr(weather, "_found", {})
    monkeypatch.setattr(weather, "_fetched_at", [])
    monkeypatch.setattr(weather, "_nominatim_down_until", 0.0)
    settings, path = Settings(), tmp_path / "places.json"
    assert asyncio.run(weather._looked_up(settings, "כפר נידח", path)) is None
    assert asyncio.run(weather._looked_up(settings, "עוד כפר", path)) is None
    assert tried == ["כפר נידח"] and "כפר נידח" not in weather._found  # asked again once the back-off is over
