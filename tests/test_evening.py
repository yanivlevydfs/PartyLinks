"""Plan the evening (partylinks.evening): nearby places open before / after an event, and the place's week."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.venues import PLACE_LISTING
from tests.test_storage_api import event, seed, settings_for

CLUB = (32.0800, 34.7800)


def _place(key: str, start: datetime, hours: float, genre: str, at: tuple[float, float], level: int | None = None) -> object:
    return event(
        key, start, title=key, url=f"https://www.happytlv.com/x/{key}", end_at=start + timedelta(hours=hours),
        latitude=at[0], longitude=at[1], source_genres=[genre],
        extra={PLACE_LISTING: True, **({"price_level": level} if level else {})},
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
        assert [t for t, _ in steps["morning"]] == ["coffee"]
        assert [e["title"] for e in out["venue"]] == ["Club X Saturday"]
        assert [t for t, _ in steps["dinner"]] == ["chef"] and out["steps"][0]["stops"][0]["event"]["price_level"] == 2
        cheap = client.get("/events/test:party/evening", params={"budget": "true"}).json()
        assert "dinner" not in {s["key"] for s in cheap["steps"]}  # ₪₪₪₪ is not a budget evening
        assert [x["event"]["title"] for x in cheap["steps"][0]["stops"]] == ["near-bar"]
        assert client.get("/events/nope/evening").status_code == 410  # an unknown / past event: gone, as its page
