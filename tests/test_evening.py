"""Plan the evening (partylinks.evening): nearby places open before / after an event, and the place's week."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.placeinfo.pages import DAYS
from partylinks.venues import PLACE_LISTING
from tests.test_storage_api import event, seed, settings_for

CLUB = (32.0800, 34.7800)


def _place(
    key: str,
    start: datetime,
    hours: float,
    genre: str,
    at: tuple[float, float],
    level: int | None = None,
    open_hours: str | None = None,
    deal: str | None = None,
) -> object:
    """A place listing for ``hours`` from ``start`` (a bar's: its happy-hour slot); ``open_hours`` its
    opening hours every day ("18:00-02:00", as extra.hours) when given."""
    return event(
        key, start, title=key, url=f"https://www.happytlv.com/x/{key}", end_at=start + timedelta(hours=hours),
        latitude=at[0], longitude=at[1], source_genres=[genre],
        extra={PLACE_LISTING: True, **({"price_level": level} if level else {}),
               **({"hours": {d: [open_hours] for d in DAYS}} if open_hours else {}), **({"deal": deal} if deal else {})},
    )  # fmt: skip


def test_the_evening_around_a_party(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    day = datetime.now(tz).date() + timedelta(days=1)
    party = datetime.combine(day, time(23), tz)
    seed(settings, [
        event("party", party, title="Techno night", venue_name="Club X", latitude=CLUB[0], longitude=CLUB[1]),
        event("next", party + timedelta(days=2), title="Club X Saturday", venue_name="Club X", latitude=CLUB[0], longitude=CLUB[1]),
        _place("near-bar", datetime.combine(day, time(19), tz), 2, "happy hour", (32.0820, 34.7810), 2),  # ~250 m
        _place("chef", datetime.combine(day, time(18), tz), 4, "fine dining", (32.0805, 34.7805), 4),  # ₪₪₪₪
        # The same restaurant from a second HappyTLV section (another page): listed once.
        event("chef-again", datetime.combine(day, time(18), tz), title="chef", url="https://www.happytlv.com/y/chef",
              end_at=datetime.combine(day, time(22), tz), latitude=32.0805, longitude=34.7805,
              source_genres=["fine dining"], extra={PLACE_LISTING: True, "price_level": 4}),
        _place("far-bar", datetime.combine(day, time(19), tz), 2, "happy hour", (32.1300, 34.7800)),  # ~5.5 km
        _place("late-bar", datetime.combine(day + timedelta(days=1), time(19), tz), 2, "happy hour", (32.0810, 34.7800)),
        _place("coffee", datetime.combine(day + timedelta(days=1), time(7), tz), 5, "coffee carts", (32.0790, 34.7790)),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        out = client.get("/events/test:party/evening").json()
        steps = {s["key"]: [(x["event"]["title"], x["distance_m"]) for x in s["stops"]] for s in out["steps"]}
        assert [t for t, _ in steps["drinks"]] == ["near-bar"]  # not 5 km away, not the next evening
        assert 150 < steps["drinks"][0][1] < 400
        assert "late" not in steps  # ends ~02:00: no bar open then; the 07:00 coffee cart is not "after a party"
        assert [e["title"] for e in out["venue"]] == ["Club X Saturday"]
        assert [t for t, _ in steps["dinner"]] == ["chef"] and out["steps"][0]["stops"][0]["event"]["price_level"] == 2
        cheap = client.get("/events/test:party/evening", params={"budget": "true"}).json()
        assert "dinner" not in {s["key"] for s in cheap["steps"]}  # ₪₪₪₪ is not a budget evening
        assert [x["event"]["title"] for x in cheap["steps"][0]["stops"]] == ["near-bar"]
        assert client.get("/events/nope/evening").status_code == 410  # an unknown / past event: gone, as its page


def test_after_a_show_a_bar_open_late_counts_not_its_happy_hour(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    day = datetime.now(tz).date() + timedelta(days=1)
    at = lambda h: datetime.combine(day, time(h), tz)
    seed(settings, [
        event("show", at(21), title="Stand-up", end_at=None, latitude=CLUB[0], longitude=CLUB[1]),  # no end: till 00:00
        _place("deal-bar", at(18), 2, "happy hour", (32.0810, 34.7810)),  # happy hour 18-20, hours unknown
        # Happy hour at noon, open till 02:00: not drinks before a 21:00 show, but a late stop after it.
        _place("night-bar", at(12), 2, "happy hour", (32.0815, 34.7805), open_hours="12:00-02:00", deal="1+1"),
        _place("early-bar", at(12), 2, "happy hour", (32.0795, 34.7795), open_hours="12:00-23:00"),  # shut at 00:00
        _place("odd-bar", at(12), 2, "happy hour", (32.0790, 34.7810), open_hours="late"),  # unreadable: skipped
        # Tonight's slot already over and deleted: tomorrow's listing stands for the bar open tonight.
        _place("gone-bar", at(12) + timedelta(days=1), 2, "happy hour", (32.0830, 34.7800), open_hours="12:00-02:00"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        out = client.get("/events/test:show/evening").json()
    steps = {s["key"]: [x["event"]["title"] for x in s["stops"]] for s in out["steps"]}
    assert steps == {"drinks": ["deal-bar"], "late": ["night-bar", "gone-bar"]}  # deal-bar's 18-20 isn't late
    late = out["steps"][-1]["stops"][0]["event"]  # shows its opening hours, not its noon happy hour
    assert (late["start_at"], late["end_at"]) == (at(12).isoformat(), (at(2) + timedelta(days=1)).isoformat())
    assert late["deal"] is None  # its noon happy hour's "1+1" is not on at midnight
