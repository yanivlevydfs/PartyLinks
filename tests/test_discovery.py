"""Weekly lines (partylinks.lines), artists like an artist (partylinks.similar) and this week's top
(partylinks.weekly_top)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.db import Database
from partylinks.popularity.models import EventViewRow
from tests.test_storage_api import event, seed, settings_for


def _night(settings: object, days: int) -> datetime:
    tz = ZoneInfo(settings.timezone)  # type: ignore[attr-defined]
    return datetime.combine(datetime.now(tz).date() + timedelta(days=days), time(23), tz)


def test_weekly_line_similar_artists_and_top(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    first = _night(settings, 1)
    db: Database = seed(settings, [
        # The same party at the same club on the same weekday, three weeks running: a line.
        *(event(f"line{w}", first + timedelta(weeks=w), title="Thursday Techno", venue_name="Club X", artists=["DJ Alpha", "DJ Beta"])
          for w in range(3)),
        event("once", first + timedelta(days=1), title="One night only", venue_name="Club X", artists=["DJ Gamma"]),
        event("other", first + timedelta(days=2), title="House at Y", venue_name="Club Y", artists=["DJ Beta", "DJ Delta"]),
        event("far", first + timedelta(days=3), title="Jazz evening", venue_name="Hall Z", artists=["Sax Man"],
              source_genres=["jazz"]),
    ])  # fmt: skip
    with db.session() as session:  # two visitors opened the one-off night's page today
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        session.add_all([EventViewRow(event_id="test:once", day=today, visitor=v) for v in ("v1", "v2")])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        items = {e["title"]: e for e in client.get("/events").json()["items"]}
        assert items["Thursday Techno"]["weekly"] == items["Thursday Techno"]["day_of_week"]
        assert items["One night only"]["weekly"] is None and items["House at Y"]["weekly"] is None
        like = [a["name"] for a in client.get("/artists/similar", params={"name": "dj alpha"}).json()["artists"]]
        assert like[0] == "DJ Beta"  # shares its lineups
        assert "DJ Alpha" not in like and client.get("/artists/similar", params={"name": "nobody"}).json()["artists"] == []
        top = client.get("/top/list").json()
        assert top["events"][0]["title"] == "One night only"
        assert client.get("/top").status_code == 200
