"""Pins from the admin lead the home rows (partylinks.home_pins): auto rows, forced rows, order, end, delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from partylinks.api import create_app
from partylinks.db import delete_events
from partylinks.home_pins import rails
from tests.test_storage_api import event, seed, settings_for

API = "/admin/api/home-pins"


@pytest.fixture
def site(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    db = seed(settings, [
        event("pin", now + timedelta(days=5), title="Techno Nature", source_genres=["techno"]),
        *(event(f"t{i}", now + timedelta(days=2), title=f"Techno {i}", source_genres=["techno"]) for i in range(4)),
        event("talk", now + timedelta(days=2), title="A lecture", source_genres=["lectures"]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        client.db = db  # type: ignore[attr-defined]
        yield client


def rows(client: TestClient) -> dict[str, list[dict]]:
    client.app.state.response_cache._items.clear()  # type: ignore[attr-defined]
    return client.get("/events/rails").json()["rails"]


def test_no_rows_chosen_leads_trending_and_the_rows_it_matches(site: TestClient) -> None:
    assert site.put(f"{API}/test:pin", json={}).status_code == 200
    r = rows(site)
    assert r["trending"][0]["id"] == "test:pin" and r["trending"][0]["pinned"] is True
    assert r["electronic"][0]["id"] == "test:pin"
    assert all(e["id"] != "test:pin" for e in r.get("lectures", []))  # not a row it doesn't match
    assert sum(e["id"] == "test:pin" for e in r["electronic"]) == 1  # once, not twice
    listed = site.get(API).json()
    assert listed[0]["title"] == "Techno Nature" and {"trending", "electronic"} <= set(listed[0]["appears"])
    assert "electronic" in site.get(f"{API}/event/test:pin").json()["natural"]


def test_chosen_rows_are_forced_and_only_those(site: TestClient) -> None:
    assert site.put(f"{API}/test:talk", json={"rails": ["electronic"]}).status_code == 200
    r = rows(site)
    assert r["electronic"][0]["id"] == "test:talk"  # though not electronic
    assert all(e["id"] != "test:talk" or not e["pinned"] for e in r["trending"])
    assert site.get(API).json()[0]["appears"] == ["electronic"]


def test_order_end_and_delete(site: TestClient) -> None:
    site.put(f"{API}/test:pin", json={})
    site.put(f"{API}/test:t1", json={})  # a new pin leads
    assert [e["id"] for e in rows(site)["trending"][:2]] == ["test:t1", "test:pin"]
    assert site.post(f"{API}/order", json=["test:pin", "test:t1"]).status_code == 200
    assert [e["id"] for e in rows(site)["trending"][:2]] == ["test:pin", "test:t1"]
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    site.put(f"{API}/test:pin", json={"until": past})
    assert [e["id"] for e in rows(site)["trending"] if e.get("pinned")] == ["test:t1"]  # past its until
    with site.db.session() as session:  # type: ignore[attr-defined]
        delete_events(session, ["test:t1"])
    assert [p["event_id"] for p in site.get(API).json()] == ["test:pin"]  # its pin went with it
    assert site.delete(f"{API}/test:pin").status_code == 204 and site.get(API).json() == []


def test_bad_requests_are_4xx_and_admin_only(site: TestClient) -> None:
    assert "no such upcoming event" in site.put(f"{API}/test:nope", json={}).json()["detail"]
    assert site.put(f"{API}/test:pin", json={"rails": ["bogus"]}).status_code == 422
    site.app.state.settings.admin.token = SecretStr("tok")  # type: ignore[attr-defined]
    assert site.get(API).status_code == 401 and site.put(f"{API}/test:pin", json={}).status_code == 401


def test_a_broken_pin_still_serves_the_rows(site: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    site.put(f"{API}/test:pin", json={})
    monkeypatch.setattr(rails, "active", lambda session: 1 / 0)
    r = rows(site)
    assert len(r["electronic"]) == 5 and not any(e.get("pinned") for e in r["electronic"])
