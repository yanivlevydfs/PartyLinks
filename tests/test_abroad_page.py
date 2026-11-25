"""The /abroad page's API: the filter's counts without the name lists, "my location" abroad, and what a probe of
the query parameters learns (nothing: no echo of the value or of the accepted ones, quotes are only text)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.models import ScrapedEvent
from tests.test_storage_api import event, seed, settings_for


def _abroad(event_id: str, city: str, lat: float, lng: float, **extra: object) -> ScrapedEvent:
    start = datetime.now(UTC) + timedelta(days=3)
    return event(
        event_id, start, venue_name=f"Hall {event_id}", address=None, city=city, country="DE",
        time_zone="Europe/Berlin", latitude=lat, longitude=lng, lineup=[f"Artist {event_id}"], **extra,
    )  # fmt: skip


def _client(tmp_path: Path) -> TestClient:
    settings = settings_for(tmp_path)
    seed(settings, [
        _abroad("lu", "Ludwigshafen", 49.4774, 8.4452),
        _abroad("ma", "Mannheim", 49.4875, 8.4660),
        _abroad("be", "Berlin", 52.5200, 13.4050),
    ])  # fmt: skip
    return TestClient(create_app(settings, start_scheduler=False))


def test_filter_counts_without_the_name_lists(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        full = client.get("/categories?abroad=true&country=DE").json()
        slim = client.get("/categories?abroad=true&country=DE&lists=false").json()
    assert full["venues"] and "lineup" in full
    assert "venues" not in slim and "lineup" not in slim
    assert {c["value"] for c in slim["cities"]} == {"Ludwigshafen", "Mannheim", "Berlin"}
    assert slim["countries"] == full["countries"] and slim["genres"] == full["genres"]


def test_my_location_abroad_is_the_nearest_city_with_events(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        here = client.get("/locate?lat=49.48&lng=8.44").json()
        berlin = client.get("/locate?lat=52.50&lng=13.38").json()
        at_sea = client.get("/locate?lat=40.0&lng=-40.0").json()
    assert (here["country"], here["abroad_city"]) == ("DE", "Ludwigshafen")
    assert berlin["abroad_city"] == "Berlin"
    assert "country" not in at_sea  # no city with events within 150 km


def test_a_probe_learns_nothing_from_the_errors(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.get("/events?abroad=true&sort=xx")
        long_code = client.get("/events?abroad=true&country=DEU")
        quoted = client.get("/events", params={"abroad": "true", "country": "DE", "city": "Ludwigshafen' OR '1'='1"})
    assert bad.status_code == 422 and long_code.status_code == 422
    for body in (bad.text, long_code.text):
        assert '"input"' not in body and '"ctx"' not in body and "start_at" not in body and "DEU" not in body
    assert bad.json()["detail"] == [{"loc": ["query", "sort"], "msg": "invalid value"}]
    assert quoted.status_code == 200 and quoted.json()["total"] == 0  # a bound value, matched as text
