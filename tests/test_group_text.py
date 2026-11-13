"""An event whose own site gives no text shows the same event's text from another site (event_text.group):
the event page (API) and the server-rendered page both, own text always first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.db import EventRow
from partylinks.event_text.group import group_description
from tests.test_storage_api import event, seed, settings_for

SOON = datetime.now(UTC) + timedelta(days=2)
LECTURE = "איך משפיעים פרויקטים של פינוי־בינוי על השכונה שלנו, והאם התחדשות עירונית היא בהכרח שיפור באיכות החיים?"


def test_an_event_without_text_shows_its_duplicates_text(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    settings.security.page_rate_limit_per_minute = settings.security.page_rate_limit_per_day = 100_000  # partylinks.page_guard
    db = seed(settings, [event("talk", SOON, title="מדע על הבר", description=None)])
    with db.session() as s:
        talk = s.get(EventRow, "test:talk")
        s.add(EventRow(**{c: getattr(talk, c) for c in ("start_at", "title")}, id="muni:talk", source="muni",
                       source_event_id="talk", url="https://m.example/talk", group_id=talk.group_id,
                       is_primary=False, description=LECTURE))  # fmt: skip
        s.flush()
        assert group_description(s, talk, settings.event_text) == LECTURE
        talk.description = "קצר"  # a few words of its own: still the full text
        assert group_description(s, talk, settings.event_text) == LECTURE
        talk.description = "ערב מדע על הבר, העיר הירוקה. מרצה: ד\"ר בני פירסט, מתכנן עירוני ומרצה באוניברסיטה העברית."  # says enough
        assert group_description(s, talk, settings.event_text) == talk.description
        settings.event_text.group_text_longer_ratio = 1.1  # but far shorter than the city's: the city's
        assert group_description(s, talk, settings.event_text) == LECTURE
        talk.description = "קצר"
        settings.event_text.group_text_min_chars = 0  # off: only its own
        assert group_description(s, talk, settings.event_text) == "קצר"
        talk.description = None
        s.commit()
    settings.event_text.group_text_min_chars = 80
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        got = c.get("/events/test:talk").json()
        assert got["description"] == LECTURE and got["description_layout"]["blocks"]
        assert "פינוי־בינוי" in c.get(f"/event/{got['slug']}").text
