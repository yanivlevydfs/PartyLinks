"""The bell never floods: re-listed shows aren't news again, and one pass gives a user a bounded number."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from partylinks.alerts import matcher
from partylinks.api import create_app
from partylinks.db import EventRow
from tests.test_accounts import OURS, FakeGoogle, _settings, sign_in
from tests.test_storage_api import event, seed

SOON = datetime.now(UTC) + timedelta(days=2)


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matcher, "SETTLE", timedelta(0))


def test_a_show_relisted_under_a_new_id_is_not_news_again(tmp_path: Path, fake: FakeGoogle) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [])
    app = create_app(settings, start_scheduler=False)
    matcher.run(settings, db)  # the cursor
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        seed(settings, [event("x", SOON, title="Techno night x")])
        assert matcher.run(settings, db) == 1
        assert c.delete("/api/me/bell", headers=OURS).status_code == 204  # the user clears it
        # The source now lists the same show under a new id; the old row goes unlisted.
        with db.session() as s:
            s.execute(update(EventRow).where(EventRow.id == "test:x").values(is_listed=False))
        seed(settings, [event("x2", SOON, title="Techno night x")])
        assert matcher.run(settings, db) == 0
        assert c.get("/api/me/bell").json()["unseen"] == 0
        # A really new show still comes.
        seed(settings, [event("z", SOON + timedelta(hours=3), title="Techno night z")])
        assert matcher.run(settings, db) == 1


def test_more_dates_of_a_known_show_are_not_news_and_a_new_show_is_one_hit(tmp_path: Path, fake: FakeGoogle) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [])
    app = create_app(settings, start_scheduler=False)
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        # A new show at three dates: one notification, the soonest date.
        seed(settings, [event(f"t{d}", SOON + timedelta(days=d), title="Techno tour") for d in (2, 0, 1)])
        assert matcher.run(settings, db) == 1
        assert [i["event"]["id"] for i in c.get("/api/me/bell").json()["items"]] == ["test:t0"]
        # The next day the source adds one more date of it: not news.
        seed(settings, [event("t9", SOON + timedelta(days=9), title="Techno tour")])
        assert matcher.run(settings, db) == 0


def test_one_pass_gives_a_user_at_most_the_cap_soonest_first(tmp_path: Path, fake: FakeGoogle) -> None:
    settings = _settings(tmp_path, max_hits_per_pass=2)
    db = seed(settings, [])
    app = create_app(settings, start_scheduler=False)
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        seed(settings, [event(k, SOON + timedelta(hours=5 - i), title=f"Techno night {k}") for i, k in enumerate("abcde")])
        assert matcher.run(settings, db) == 2
        bell = c.get("/api/me/bell").json()
        assert bell["unseen"] == 2 and {i["event"]["id"] for i in bell["items"]} == {"test:e", "test:d"}  # the soonest


def test_a_day_gives_a_user_at_most_the_daily_cap(tmp_path: Path, fake: FakeGoogle) -> None:
    """A source back after days of failures lists its backlog at once: the bell still grows by a few a day."""
    settings = _settings(tmp_path, max_hits_per_pass=3, max_hits_per_day=4)
    db = seed(settings, [])
    app = create_app(settings, start_scheduler=False)
    matcher.run(settings, db)
    with TestClient(app) as c:
        sign_in(c, fake)
        c.post("/api/me/alerts", json={"criteria": {"q": "techno"}}, headers=OURS)
        seed(settings, [event(k, SOON, title=f"Techno night {k}") for k in "abc"])
        assert matcher.run(settings, db) == 3
        seed(settings, [event(k, SOON, title=f"Techno night {k}") for k in "defg"])
        assert matcher.run(settings, db) == 1  # 3 + 1 = the day's 4
        seed(settings, [event("h", SOON, title="Techno night h")])
        assert matcher.run(settings, db) == 0
        assert c.get("/api/me/bell").json()["unseen"] == 4
