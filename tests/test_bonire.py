"""Bonire: the category grid's JSON, a movie page's screenings and an event page's JSON-LD, on trimmed
real answers (26.11.2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.bonire import BonireSettings, grid_events, locale_url, page_events, wall_clock

FIXTURES = Path(__file__).parent / "fixtures" / "bonire"
TZ = "Asia/Jerusalem"
SETTINGS = BonireSettings(cron="7 2,14 * * *")


def _rows() -> dict[str, dict[str, Any]]:
    grid = json.loads((FIXTURES / "grid.json").read_text(encoding="utf-8"))
    return {item["data"]["id"]: item["data"] for item in grid["items"]}


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_party_from_the_grid() -> None:
    row = _rows()["5846f26c-536e-495b-a48c-e8d840fb934d"]
    (event,) = grid_events(row, {"parties", "music"}, {}, SETTINGS, TZ)
    assert event.title == "Lushi"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-26T00:00:00+02:00"
    assert event.venue_name and event.city
    assert event.latitude is not None and event.longitude is not None
    assert "מסיבות" in event.source_genres and "מוזיקה" in event.source_genres
    assert event.extra["purchase_url"] == row["ticketUrl"]
    assert event.images and all(i.startswith("https://bonire.live/api/images/") for i in event.images)
    assert event.artists
    assert str(event.url) == "https://bonire.live/he/event/e-5846f26c-536e-495b-a48c-e8d840fb934d"


def test_sitemap_url_is_the_event_page() -> None:
    row = _rows()["5846f26c-536e-495b-a48c-e8d840fb934d"]
    page = "https://bonire.live/he/event/lushi-5846f26c-536e-495b-a48c-e8d840fb934d"
    (event,) = grid_events(row, {"parties"}, {row["id"]: page}, SETTINGS, TZ)
    assert str(event.url) == page


def test_show_gives_one_event_per_date_on_its_show_page() -> None:
    row = _rows()["12d753a0-cee4-4608-9f93-ea9f090c8308"]
    events = grid_events(row, {"kids"}, {}, SETTINGS, TZ)
    assert [e.source_event_id for e in events] == [o["id"] for o in row["occurrences"]]
    route = row["showRoute"]
    assert all(str(e.url) == f"https://bonire.live/he/show/{route['slug']}-{route['publicId']}" for e in events)
    assert all(e.end_at is None for e in events)  # a show's run end is no date's end
    assert "ילדים" in events[0].source_genres


def test_exhibition_keeps_its_run_end_and_free_entry_is_price_zero() -> None:
    rows = _rows()
    (exhibition,) = grid_events(rows["5d7cc577-cb05-4bc7-afe4-491cd5e73682"], {"exhibitions"}, {}, SETTINGS, TZ)
    assert exhibition.end_at is not None and exhibition.start_at is not None and exhibition.end_at > exhibition.start_at
    assert "תערוכות" in exhibition.source_genres
    (free,) = grid_events(rows["b52a4c96-9b4e-497a-9d11-64604385ae92"], {"exhibitions"}, {}, SETTINGS, TZ)
    assert free.min_price == Decimal(0)


def test_movie_page_screenings() -> None:
    url = "https://bonire.live/he/movie/vagabond-004e40014170"
    events = page_events(_page("movie.html"), url, SETTINGS, TZ)
    assert events
    first = events[0]
    assert first.title == "עוברת אורח"
    assert first.source_event_id == "f05fe26d-ee88-4439-b5a2-e30d700141f7"
    assert first.start_at is not None and first.start_at.isoformat() == "2026-12-03T21:00:00+02:00"
    assert first.end_at is not None and first.end_at.isoformat() == "2026-12-03T22:46:00+02:00"
    assert first.venue_name == "בית רדיקל" and first.city == "תל אביב"
    assert first.latitude is not None
    assert str(first.url) == url
    assert first.extra["purchase_url"] == "https://radical.org.il/product/vagabond/"
    assert first.min_price == Decimal(40)
    assert "קולנוע" in first.source_genres


def test_event_page() -> None:
    url = "https://bonire.live/he/event/adam-ten-darco-roots-reading-3-d9c30042-e7b2-4972-b6c3-817c1fd394ac"
    (event,) = page_events(_page("event.html"), url, SETTINGS, TZ)
    assert event.source_event_id == "d9c30042-e7b2-4972-b6c3-817c1fd394ac"
    assert event.title == "Adam Ten • Darco | Reading 3 | 27.11"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-27T23:00:00+02:00"
    assert event.venue_name == "Reading 3" and event.city == "תל אביב"
    assert event.extra["purchase_url"] == "https://muzi.co.il/event/adam-ten-darco-reading-3/"
    assert event.min_price == Decimal(199) and event.max_price == Decimal(530)
    assert event.artists == ["אדם טן", "Darco"]
    assert "מוזיקה" in event.source_genres


def test_hour_24_and_a_broken_offset_are_the_next_day() -> None:
    start, timed = wall_clock("2026-11-27T24:20:00+26:00", TZ)
    assert timed and start is not None and start.isoformat() == "2026-11-28T00:20:00+02:00"
    day, timed = wall_clock("2026-12-01", TZ)
    assert not timed and day is not None and day.date().isoformat() == "2026-12-01"
    assert wall_clock("soon", TZ) == (None, False)


def test_locale_copies_fold_to_the_hebrew_page() -> None:
    for url in (
        "https://bonire.live/artist/x-1",
        "https://bonire.live/ru/artist/x-1",
        "https://bonire.live/he/artist/x-1",
    ):
        assert locale_url(url, SETTINGS) == "https://bonire.live/he/artist/x-1"


def test_enrichment_gives_site_genres() -> None:
    enricher = Enricher(Settings().enrichment, TZ)
    rows = _rows()
    events = [
        *grid_events(rows["5846f26c-536e-495b-a48c-e8d840fb934d"], {"parties"}, {}, SETTINGS, TZ),
        *page_events(_page("movie.html"), "https://bonire.live/he/movie/vagabond-004e40014170", SETTINGS, TZ),
    ]
    for event in events:
        enriched = enricher.enrich(event)
        assert enriched is not None and enriched.genres, event.title
