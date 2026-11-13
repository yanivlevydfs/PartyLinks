"""The /promote form (partylinks.promote): upcoming events only, a valid mobile, the hidden field, the admin's list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_storage_api import event, seed, settings_for

API = "/api/promote-requests"
OURS = {"X-Requested-With": "spotx"}


@pytest.fixture
def site(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("soon", now + timedelta(days=3), title="Techno Nature"),
        event("later", now + timedelta(days=9), title="Jazz Night"),
        event("past", now - timedelta(days=3), title="Last week"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        yield client


def form(**over: Any) -> dict[str, Any]:
    return {"name": "Dana Cohen", "business": "Night Owls", "email": "dana@example.com", "phone": "052-538-1648",
            "event_ids": ["test:soon", "test:later"], "placements": ["rail", "results"], "message": "Two weekends"} | over  # fmt: skip


def test_a_request_is_saved_with_its_events_and_listed_for_the_admin(site: TestClient) -> None:
    res = site.post(API, json=form(), headers=OURS)
    assert res.status_code == 201, res.text
    [r] = site.get("/admin/api/promote-requests").json()
    assert r["phone"] == "+972525381648" and r["status"] == "new" and r["placements"] == ["rail", "results"]
    assert [e["title"] for e in r["events"]] == ["Techno Nature", "Jazz Night"]
    assert (
        site.patch(f"/admin/api/promote-requests/{r['id']}", json={"status": "contacted"}).json()["status"]
        == "contacted"
    )
    assert site.delete(f"/admin/api/promote-requests/{r['id']}").status_code == 204
    assert site.get("/admin/api/promote-requests").json() == []


@pytest.mark.parametrize(
    "over",
    [
        {"event_ids": ["test:past"]},  # over: never promoted
        {"event_ids": ["test:nope"]},
        {"event_ids": []},
        {"phone": "03-6123456"},  # a landline, not a mobile
        {"phone": "12345"},
        {"email": "not-an-email"},
        {"name": ""},
        {"placements": ["billboard"]},
    ],
)
def test_bad_requests_are_refused(site: TestClient, over: dict[str, Any]) -> None:
    assert site.post(API, json=form(**over), headers=OURS).status_code == 422
    assert site.get("/admin/api/promote-requests").json() == []


def test_other_sites_and_bots_get_nothing_saved(site: TestClient) -> None:
    assert site.post(API, json=form()).status_code == 403  # no X-Requested-With: not our page
    assert site.post(API, json=form(website="http://spam"), headers=OURS).status_code == 201  # quiet "thanks"
    assert site.get("/admin/api/promote-requests").json() == []
