"""The /contact form (partylinks.contact): no sign-in, saved for the admin's tab and never emailed, bots and
other sites refused."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_accounts import ADMIN, FakeGoogle, _settings, sign_in

API = "/api/contact"
OURS = {"X-Requested-With": "spotx"}
FORM = {"name": "Dana Cohen", "email": "dana@example.com", "subject": "Wrong date", "message": "The party is on Friday",
        "page": "https://spotx.fun/event/x"}  # fmt: skip


@pytest.fixture
def site(tmp_path: Path, fake: FakeGoogle, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from partylinks.mail import send as mailer

    settings = _settings(tmp_path)
    settings.mail = settings.mail.model_validate({"resend_api_key": "re_test"})
    mails: list[Any] = []
    monkeypatch.setattr(mailer, "send", lambda cfg, messages, key: mails.extend(messages))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        client.fake, client.mails = fake, mails  # type: ignore[attr-defined]
        yield client


def admin_list(site: TestClient) -> list[dict[str, Any]]:
    sign_in(site, site.fake, email=ADMIN, sub="g-admin")  # type: ignore[attr-defined]
    return site.get("/admin/api/contact-messages").json()


def test_a_message_is_saved_signed_out_for_the_admin_and_emails_nobody(site: TestClient) -> None:
    assert site.post(API, json=FORM, headers=OURS).status_code == 201
    [m] = admin_list(site)
    assert m["email"] == "dana@example.com" and m["subject"] == "Wrong date" and m["status"] == "new"
    assert site.mails == []  # type: ignore[attr-defined]
    assert (
        site.patch(f"/admin/api/contact-messages/{m['id']}", json={"status": "answered"}, headers=OURS).json()["status"]
        == "answered"
    )
    assert site.delete(f"/admin/api/contact-messages/{m['id']}", headers=OURS).status_code == 204
    assert site.get("/admin/api/contact-messages").json() == []


@pytest.mark.parametrize("over", [{"email": "nope"}, {"name": ""}, {"message": "hi"}, {"page": "javascript:alert(1)"}])
def test_bad_messages_are_refused(site: TestClient, over: dict[str, str]) -> None:
    assert site.post(API, json=FORM | over, headers=OURS).status_code == 422
    assert admin_list(site) == []


def test_other_sites_and_bots_save_nothing(site: TestClient) -> None:
    assert site.post(API, json=FORM).status_code == 403
    assert site.post(API, json=FORM | {"website": "http://spam"}, headers=OURS).status_code == 201
    assert admin_list(site) == []
