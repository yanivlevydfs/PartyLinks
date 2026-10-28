""""Who's coming with me?" plans: create, answer, change an answer, privacy, limits, clean-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.db import delete_events
from partylinks.plans.models import PlanEventRow, PlanRow, PlanVoteRow
from partylinks.plans.routes import MAX_OPTIONS, MAX_VOTES
from tests.test_storage_api import event, seed, settings_for

ME, FRIEND = "voter-aaaa-1111", "voter-bbbb-2222"


def _app(tmp_path: Path):
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    db = seed(settings, [event(k, datetime.now(UTC) + timedelta(days=2, hours=i)) for i, k in enumerate("abcde")])
    return create_app(settings, start_scheduler=False), db


def test_plan_answers_and_privacy(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/plans", json={"event_id": "nope"}).status_code == 404
        plan = c.post("/plans", json={"event_id": "test:a"}).json()
        pid = plan["id"]
        assert len(pid) >= 8 and plan["event"]["title"] == "Techno night a" and plan["votes"] == []

        c.post(f"/plans/{pid}/votes", json={"voter": ME, "name": "  יניב  ", "answer": "yes"})
        c.post(f"/plans/{pid}/votes", json={"voter": FRIEND, "name": "דנה", "answer": "maybe"})
        changed = c.post(f"/plans/{pid}/votes", json={"voter": FRIEND, "name": "דנה", "answer": "no"}).json()
        assert [(v["name"], v["answer"], v["mine"]) for v in changed["votes"]] == [("יניב", "yes", False), ("דנה", "no", True)]

        mine = c.get(f"/plans/{pid}", params={"me": ME}).json()
        assert [v["mine"] for v in mine["votes"]] == [True, False]
        assert "voter" not in str(mine) and ME not in str(mine)  # nobody sees another browser's key
        assert c.get(f"/plans/{pid}").json()["votes"][0]["mine"] is False

        assert c.post(f"/plans/{pid}/votes", json={"voter": ME, "name": " ", "answer": "yes"}).status_code == 422
        assert c.post(f"/plans/{pid}/votes", json={"voter": ME, "name": "x", "answer": "sure"}).status_code == 422
        assert c.post(f"/plans/{pid}/votes", json={"voter": "a b", "name": "x", "answer": "yes"}).status_code == 422
        assert c.post("/plans/missing/votes", json={"voter": ME, "name": "x", "answer": "yes"}).status_code == 404

        page = c.get(f"/plan/{pid}")  # the link preview WhatsApp reads (when the site is built)
        assert page.status_code in (200, 404)
        if page.status_code == 200 and "<head" in page.text:
            assert "noindex" in page.text and "Techno night a" in page.text


def test_plan_is_capped_and_goes_with_its_event(tmp_path: Path) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        pid = c.post("/plans", json={"event_id": "test:a"}).json()["id"]
        for i in range(MAX_VOTES):
            assert c.post(f"/plans/{pid}/votes", json={"voter": f"voter-{i:08d}", "name": f"n{i}", "answer": "yes"}).status_code == 200
        assert c.post(f"/plans/{pid}/votes", json={"voter": "voter-overflow", "name": "x", "answer": "yes"}).status_code == 409
        assert c.post(f"/plans/{pid}/votes", json={"voter": "voter-00000000", "name": "n0", "answer": "no"}).status_code == 200

        with db.session() as session:
            delete_events(session, ["test:a"])
        with db.session(write=False) as session:
            assert session.query(PlanRow).count() == 0 and session.query(PlanVoteRow).count() == 0
        assert c.get(f"/plans/{pid}").status_code == 404


def test_which_party_and_who_drives(tmp_path: Path) -> None:
    app, db = _app(tmp_path)
    with TestClient(app) as c:
        pid = c.post("/plans", json={"event_id": "test:a"}).json()["id"]
        plan = c.post(f"/plans/{pid}/events", json={"event_id": "test:b"}).json()
        assert [o["id"] for o in plan["options"]] == ["test:a", "test:b"] and plan["event"]["id"] == "test:a"
        assert len(c.post(f"/plans/{pid}/events", json={"event_id": "test:b"}).json()["options"]) == 2  # no twice
        for k in "cd":
            c.post(f"/plans/{pid}/events", json={"event_id": f"test:{k}"})
        assert c.post(f"/plans/{pid}/events", json={"event_id": "test:e"}).status_code == 409  # MAX_OPTIONS
        assert MAX_OPTIONS == 4
        assert c.post(f"/plans/{pid}/events", json={"event_id": "nope"}).status_code == 404

        vote = {"voter": ME, "name": "יניב", "answer": "yes", "picks": ["test:b", "test:x", "test:b"], "ride": "drive", "seats": 3}
        me = next(v for v in c.post(f"/plans/{pid}/votes", json=vote).json()["votes"] if v["mine"])
        assert me["picks"] == ["test:b"] and me["ride"] == "drive" and me["seats"] == 3  # unknown and repeated picks dropped
        friend = {"voter": FRIEND, "name": "דנה", "answer": "no", "picks": ["test:a"], "ride": "need"}
        dana = next(v for v in c.post(f"/plans/{pid}/votes", json=friend).json()["votes"] if v["mine"])
        assert dana["picks"] == [] and dana["ride"] is None  # not coming: no picks, no ride
        assert c.post(f"/plans/{pid}/votes", json=vote | {"seats": 9}).status_code == 422
        need = c.post(f"/plans/{pid}/votes", json=vote | {"ride": "need", "seats": 4}).json()
        assert next(v for v in need["votes"] if v["mine"])["seats"] is None  # seats only with a car

        with db.session() as session:  # a compared event is over: it leaves the options, the plan stays
            delete_events(session, ["test:b"])
        left = c.get(f"/plans/{pid}", params={"me": ME}).json()
        assert [o["id"] for o in left["options"]] == ["test:a", "test:c", "test:d"]
        assert next(v for v in left["votes"] if v["mine"])["picks"] == []
        with db.session() as session:  # the plan's own event is over: everything goes
            delete_events(session, ["test:a"])
        with db.session(write=False) as session:
            assert session.query(PlanRow).count() == session.query(PlanEventRow).count() == session.query(PlanVoteRow).count() == 0
