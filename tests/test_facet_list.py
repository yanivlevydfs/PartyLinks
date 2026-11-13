"""The venues / artists indexes a page at a time (partylinks.facet_list): search, filter, paging."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.facet_list.routes import artist_filter, search
from tests.test_storage_api import event, seed, settings_for


def f(value: str, events: int = 1, **extra: object) -> dict[str, object]:
    return {"value": value, "events": events, **extra}


def test_search_ranks_whole_name_then_prefix_then_word_then_anywhere() -> None:
    items = [f("The Barby Club", 9), f("Barbylon", 2), f("Barby", 1), f("Club Barby", 5), f("Nothing")]
    found, fuzzy = search(items, "barby")
    assert [x["value"] for x in found] == ["Barby", "Barbylon", "The Barby Club", "Club Barby"]
    assert not fuzzy


def test_search_drops_a_typo_at_the_end() -> None:
    found, fuzzy = search([f("Gray Club"), f("Other")], "graycluxx")
    assert [x["value"] for x in found] == ["Gray Club"] and fuzzy


def test_artist_filter_counts_each_row_by_the_others() -> None:
    facets = [f("A"), f("B"), f("C")]
    tags = {"a": {"g": ["g_parties", "techno"], "w": ["tel_aviv"]}, "b": {"g": ["g_live"], "w": ["DE"]},
            "c": {"g": ["g_parties"], "w": ["DE"]}}  # fmt: skip
    groups = [{"value": "g_parties", "events": 2, "members": ["techno"]}, {"value": "g_live", "events": 1}]
    kept, chips = artist_filter(facets, tags, groups, "g_parties", None, "abroad", None)
    assert [x["value"] for x in kept] == ["C"]
    assert dict(chips["cats"]) == {"g_parties": 1, "g_live": 1}  # abroad: C and B
    assert chips["here"] == 1 and chips["away"] == 1  # g_parties: A (here) and C (abroad)
    assert chips["places"] == [("DE", 1)]


def test_venues_page_and_search(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    night = datetime.combine(datetime.now(ZoneInfo(settings.timezone)).date() + timedelta(days=1), time(23),
                             ZoneInfo(settings.timezone))  # fmt: skip
    seed(settings, [event(f"v{i}", night, title=f"Night {i}", venue_name=f"Venue {i:02d}") for i in range(5)]
         + [event("junk", night, title="Junk", venue_name="-")])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        first = client.get("/facets/venues?page_size=2").json()
        second = client.get("/facets/venues?page_size=2&page=2").json()
        found = client.get("/facets/venues?q=venue03").json()
    assert first["total"] == 5  # "-" is no venue
    assert [v["value"] for v in first["items"] + second["items"]] == ["Venue 00", "Venue 01", "Venue 02", "Venue 03"]
    assert [v["value"] for v in found["items"]] == ["Venue 03"]


def test_artists_page_carries_the_filter_chips(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.site.artists_verified_only = False
    night = datetime.combine(datetime.now(ZoneInfo(settings.timezone)).date() + timedelta(days=1), time(23),
                             ZoneInfo(settings.timezone))  # fmt: skip
    seed(settings, [event("t", night, title="Techno", artists=["DJ Alpha"], source_genres=["techno"]),
                    event("j", night, title="Jazz", artists=["Sax Man"], source_genres=["jazz"], country="DE")])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/facets/artists").json()
        abroad = client.get("/facets/artists?where=abroad").json()
    assert [a["value"] for a in page["items"]] == ["DJ Alpha", "Sax Man"]
    assert page["chips"]["here"] == 1 and page["chips"]["away"] == 1
    assert [a["value"] for a in abroad["items"]] == ["Sax Man"]
