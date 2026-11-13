"""The /promote form (partylinks.promote): signed-in users only, upcoming events only, a valid mobile, the hidden
field, the admin's list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_accounts import ADMIN, FakeGoogle, _settings, sign_in
from tests.test_storage_api import event, seed

API = "/api/promote-requests"
OURS = {"X-Requested-With": "spotx"}


@pytest.fixture
def site(tmp_path: Path, fake: FakeGoogle, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Signed in as an ordinary user, Dana. Mail is set up; what is sent lands in ``client.mails``."""
    from partylinks.mail import send as mailer

    settings = _settings(tmp_path)
    settings.mail = settings.mail.model_validate({"resend_api_key": "re_test"})
    mails: list[Any] = []
    monkeypatch.setattr(mailer, "send", lambda cfg, messages, key: mails.extend(messages))
    now = datetime.now(UTC).replace(microsecond=0)
    seed(settings, [
        event("soon", now + timedelta(days=3), title="Techno Nature", venue_name="Night Owls Club"),
        event("later", now + timedelta(days=9), title="Jazz Night"),
        event("past", now - timedelta(days=3), title="Last week"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        client.fake, client.mails = fake, mails  # type: ignore[attr-defined]
        sign_in(client, fake)
        yield client


def as_admin(site: TestClient) -> TestClient:
    sign_in(site, site.fake, email=ADMIN, sub="g-admin")  # type: ignore[attr-defined]
    return site


def form(**over: Any) -> dict[str, Any]:
    return {"name": "Dana Cohen", "business": "Night Owls", "email": "dana@example.com", "phone": "052-538-1648",
            "event_ids": ["test:soon", "test:later"], "placements": ["rail", "results"], "message": "Two weekends"} | over  # fmt: skip


def test_a_request_is_saved_with_its_events_and_listed_for_the_admin(site: TestClient) -> None:
    res = site.post(API, json=form(), headers=OURS)
    assert res.status_code == 201, res.text
    user = site.get("/api/me").json()["id"]
    [r] = as_admin(site).get("/admin/api/promote-requests").json()
    assert r["user_id"] == user
    [mail] = site.mails  # type: ignore[attr-defined]
    assert mail.to == ADMIN and mail.reply_to == "dana@example.com" and "Techno Nature" in mail.text
    assert r["phone"] == "+972525381648" and r["status"] == "new" and r["placements"] == ["rail", "results"]
    assert [e["title"] for e in r["events"]] == ["Techno Nature", "Jazz Night"]
    assert (
        site.patch(f"/admin/api/promote-requests/{r['id']}", json={"status": "contacted"}, headers=OURS).json()[
            "status"
        ]
        == "contacted"
    )
    assert site.delete(f"/admin/api/promote-requests/{r['id']}", headers=OURS).status_code == 204
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
    assert as_admin(site).get("/admin/api/promote-requests").json() == []


def test_signed_out_visitors_other_sites_and_bots_get_nothing_saved(site: TestClient) -> None:
    assert site.post(API, json=form()).status_code == 403  # no X-Requested-With: not our page
    assert site.post(API, json=form(website="http://spam"), headers=OURS).status_code == 201  # quiet "thanks"
    site.post("/api/auth/logout", headers=OURS)
    assert site.post(API, json=form(), headers=OURS).status_code == 401  # signed out
    assert as_admin(site).get("/admin/api/promote-requests").json() == []


def test_the_picker_finds_upcoming_events_by_every_word(site: TestClient) -> None:
    pick = lambda q: [e["title"] for e in site.get(f"{API}/events", params={"q": q}).json()]
    assert pick("night") == ["Techno Nature", "Jazz Night"]  # by venue, by title; soonest first
    assert pick("jazz night") == ["Jazz Night"] and pick("techno") == ["Techno Nature"]
    assert pick("last week") == []  # over: never offered
    assert site.get(f"{API}/events", params={"q": "x"}).status_code == 422  # two characters at least
    site.post("/api/auth/logout", headers=OURS)
    assert site.get(f"{API}/events", params={"q": "jazz"}).status_code == 401  # signed in, as the form
