"""Sponsored events first in the results (GET /events?sponsored=true)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import update

from partylinks.api import create_app
from partylinks.db import EventRow
from tests.test_storage_api import event, seed, settings_for


def test_paid_matches_lead_page_one_only_when_they_match(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.api.sponsored_results = 3
    soon = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [
        event(f"e{i}", soon + timedelta(hours=i), title=f"Techno night {i}" if i % 2 else f"Jazz evening {i}")
        for i in range(10)
    ])  # fmt: skip
    with db.session() as session:  # e9 is another listing of e7's event: the group's primary card shows
        session.execute(update(EventRow).where(EventRow.id == "test:e9").values(group_id="test:e7", is_primary=False))
    with TestClient(create_app(settings, start_scheduler=False)) as client:

        def page(qs: str) -> dict:
            client.app.state.response_cache._items.clear()  # type: ignore[attr-defined]
            return client.get(f"/events?page_size=4&{qs}").json()

        def ids(body: dict) -> list[str]:
            return [e["source_event_id"] for e in body["items"]]

        plain = [page(f"page={n}") for n in (1, 2, 3)]
        # Nothing sponsored for results yet (rails picks never jump in): exactly the plain list.
        client.put("/admin/api/sponsored", json=[{"event_id": "test:e5", "side": "right"}])
        client.post("/admin/api/sponsored/enabled", json={"on": True})
        assert [page(f"page={n}&sponsored=true") for n in (1, 2, 3)] == plain
        assert all(e["sponsored"] is None for p in plain for e in p["items"])

        picks = [
            {"event_id": "test:e8", "side": "right", "placement": "results"},
            {"event_id": "test:e3", "side": "left", "placement": "both"},
            {"event_id": "test:e9", "side": "left", "placement": "results"},  # e7's group
            {"event_id": "test:e1", "side": "left", "placement": "results"},
            {
                "event_id": "test:e0",
                "side": "right",
                "until": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "placement": "results",
            },
        ]
        assert client.put("/admin/api/sponsored", json=picks).status_code == 200
        first = page("page=1&sponsored=true")
        # By start time, at most 3, marked with the placement's id; e0's campaign is over.
        assert ids(first)[:3] == ["e1", "e3", "e7"]
        assert [e["sponsored"] for e in first["items"]] == ["test:e1", "test:e3", "test:e9", None]
        # Never twice in the same results, and the paging covers every match exactly once.
        pages = [first] + [page(f"page={n}&sponsored=true") for n in (2, 3)]
        seen = [i for p in pages for i in ids(p)]
        assert sorted(seen) == sorted(i for p in plain for i in ids(p)) and len(seen) == len(set(seen))
        assert first["total"] == plain[0]["total"]
        assert all(e["sponsored"] is None for p in pages[1:] for e in p["items"])
        # Only the picks that pass the filters: e8 is a jazz evening, the others techno nights.
        jazz = page("q=jazz&sponsored=true")
        assert [e["sponsored"] for e in jazz["items"]] == ["test:e8", None, None, None]
        techno = page("q=techno&sponsored=true")
        assert [e["sponsored"] for e in techno["items"][:3]] == ["test:e1", "test:e3", "test:e9"]
        assert page("q=techno")["items"][0]["sponsored"] is None  # other callers: the plain list
        # The rails keep only their own picks.
        rails = client.get("/events/sponsored").json()
        assert [e["id"] for e in rails["left"]] == ["test:e3"] and rails["right"] == []

        settings.api.sponsored_results = 1
        assert [e["sponsored"] for e in page("page=1&sponsored=true")["items"]] == ["test:e1", None, None, None]
        # The switch off hides them all again.
        client.post("/admin/api/sponsored/enabled", json={"on": False})
        assert [page(f"page={n}&sponsored=true") for n in (1, 2, 3)] == plain

        client.post("/admin/api/sponsored/enabled", json={"on": True})
        client.post("/events/sponsored/hit", json={"event_id": "test:e9", "kind": "view", "where": "results"})
        client.post("/events/sponsored/hit", json={"event_id": "test:e9", "kind": "click", "where": "results"})
        client.post("/events/sponsored/hit", json={"event_id": "test:e3", "kind": "view"})
        report = client.get("/admin/api/sponsored/stats?days=1").json()
        assert (report["views"], report["clicks"], report["results_views"], report["results_clicks"]) == (2, 1, 1, 1)
        assert [s["placement"] for s in client.get("/admin/api/sponsored").json()["items"]][:1] == ["both"]



def test_a_pick_goes_with_its_event(tmp_path: Path) -> None:
    """Retention deleting an ended event removes its sponsored pick (and picks orphaned earlier); the
    advertiser report keeps its numbers."""
    from sqlalchemy import func, select

    from partylinks.ads.models import SponsoredRow, SponsoredStatRow
    from partylinks.db import delete_events

    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event(k, soon) for k in ("a", "b")])
    with db.session() as s:
        s.add_all([SponsoredRow(event_id="test:a", side="right"), SponsoredRow(event_id="test:b", side="left"),
                   SponsoredRow(event_id="test:gone-long-ago", side="left")])  # fmt: skip
        s.add(SponsoredStatRow(event_id="test:a", day=soon.date(), title="Techno night a", views=5, clicks=1))
    with db.session() as s:
        delete_events(s, ["test:a"])
    with db.session() as s:
        assert set(s.scalars(select(SponsoredRow.event_id))) == {"test:b"}
        assert s.scalar(select(func.sum(SponsoredStatRow.views))) == 5
