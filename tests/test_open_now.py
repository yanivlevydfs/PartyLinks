"""Open now: the "now" filter (/events?open_now=true) and the /now page."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_storage_api import event, seed, settings_for


def test_open_now_lists_what_runs_at_this_hour(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(UTC).replace(microsecond=0)
    midnight = datetime.combine(now.astimezone(tz).date(), time(), tz)
    seed(settings, [
        event("running", now - timedelta(hours=1), title="Happy hour running", end_at=now + timedelta(hours=2)),
        event("later", now + timedelta(hours=3), title="Party later", end_at=now + timedelta(hours=8)),
        # A place listed all day (its page gives no hours): not known to be open at this hour.
        event("allday", midnight, title="Escape room all day", end_at=None),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        titles = {e["title"] for e in client.get("/events", params={"open_now": "true"}).json()["items"]}
        assert titles == {"Happy hour running"}
        assert len(client.get("/events").json()["items"]) == 3  # the filter is off by default
        page = client.get("/now")
        assert page.status_code == 200 and "Happy hour running" in page.text and "Party later" not in page.text
        assert "/now" in client.get("/sitemap-1.xml").text
