"""Place cards: a place listed once a day is one card (its first matching opening), with its deal."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.venues import PLACE_LISTING
from tests.test_storage_api import event, seed, settings_for

BAR = "https://www.happytlv.com/tel-aviv-happy-hours/bar"


def test_a_place_is_one_card_with_its_deal(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    day = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time(18), tz)
    bar = [
        event(f"hh-{n}", day + timedelta(days=n), title="האפי האוור בבר", url=BAR, end_at=day + timedelta(days=n, hours=2),
              latitude=32.08, longitude=34.78, source_genres=["happy hour"], extra={PLACE_LISTING: True, "deal": "1+1 על שתייה"})
        for n in range(3)
    ]  # fmt: skip
    seed(settings, [*bar, event("party", day + timedelta(hours=5), title="Techno night", latitude=32.07, longitude=34.77)])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        items = client.get("/events").json()["items"]
        places = [e for e in items if e["place"]]
        assert len(items) == 2 and len(places) == 1
        assert places[0]["deal"] == "1+1 על שתייה" and places[0]["start_at"].startswith(day.date().isoformat())
        assert not next(e for e in items if not e["place"])["deal"]
        # A day's filter still finds that day's opening.
        third = (day + timedelta(days=2)).date().isoformat()
        assert [e["local_date"] for e in client.get("/events", params={"date": third}).json()["items"] if e["place"]] == [third]
        assert len(client.get("/events/map").json()["items"]) == 2  # one pin for the bar
        assert client.get("/genre/happy_hour").text.count("האפי האוור בבר") <= 2  # the list once (+ the title tag's FAQ)


def test_places_hub_counts_each_place_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    day = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time(18), tz)
    rows = [
        event(f"{slug}-{n}", day + timedelta(days=n), title=slug, url=f"https://www.happytlv.com/x/{slug}",
              end_at=day + timedelta(days=n, hours=2), source_genres=genres, extra={PLACE_LISTING: True})
        for slug, genres in (("bar", ["happy hour", "kosher"]), ("pub", ["happy hour"])) for n in range(3)
    ]  # fmt: skip
    seed(settings, [*rows, event("party", day, title="Techno night")])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        cats = {c["genre"]: c["places"] for c in client.get("/places/categories").json()["categories"]}
        assert cats == {"happy_hour": 2, "kosher": 1}  # places, not their days; events are not places
        page = client.get("/places")
        assert page.status_code == 200 and "מקומות כשרים" in page.text and 'href="/now"' in page.text
        assert "/places" in client.get("/sitemap-1.xml").text
