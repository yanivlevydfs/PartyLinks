"""Box offices: each Smarticket tenant counted by the host of its events' URLs (no database needed)."""

from __future__ import annotations

from typing import Any

from partylinks.box_offices import box_offices
from partylinks.scrapers.smarticket import SmarticketSettings, SmarticketTenant

SETTINGS = SmarticketSettings(
    cron="8 */4 * * *",
    tenant_domain="smarticket.co.il",
    shows_path="/api/shows",
    event_page_path="/event/{id}",
    uploads_path="/uploads/",
    calendar_path="/api/show_theater/get_events_calendar",
    tenants=[
        SmarticketTenant(host="htk", venue_name="היכל התרבות כרמיאל", city="כרמיאל"),
        SmarticketTenant(host="tickets.raanana.muni.il", venue_name="רעננה", city="רעננה"),
        SmarticketTenant(host="dolev", venue_name='מתנ"ס קריית אונו'),
    ],
)


class _Session:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows

    def execute(self, _: Any) -> _Session:
        return self

    def all(self) -> list[tuple[str, str]]:
        return self.rows


def test_counts_each_event_group_once_per_tenant() -> None:
    rows = [
        ("g1", "https://htk.smarticket.co.il/event/1"),
        ("g1", "https://htk.smarticket.co.il/event/1"),  # the same group twice: one event
        ("g2", "https://htk.smarticket.co.il/event/2"),
        ("g3", "https://tickets.raanana.muni.il/event/9"),
    ]
    offices = box_offices(_Session(rows), {"smarticket": SETTINGS})  # type: ignore[arg-type]
    assert [(o.name, o.events, o.url) for o in offices] == [
        ("היכל התרבות כרמיאל", 2, "https://htk.smarticket.co.il"),
        ("רעננה", 1, "https://tickets.raanana.muni.il"),
        ('מתנ"ס קריית אונו', 0, "https://dolev.smarticket.co.il"),  # no upcoming events yet: still listed
    ]
    assert offices[0].city == "כרמיאל" and offices[2].city is None


def test_no_smarticket_source_no_offices() -> None:
    assert box_offices(_Session([]), {}) == []  # type: ignore[arg-type]
    off = SETTINGS.model_copy(update={"enabled": False})
    assert box_offices(_Session([]), {"smarticket": off}) == []  # type: ignore[arg-type]
