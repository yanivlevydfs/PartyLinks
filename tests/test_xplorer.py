"""Xplorer parser tests against trimmed real pages: the shows board, two artist pages, one event page."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from partylinks.parsing import ParseError
from partylinks.scrapers import xplorer

FIXTURES = Path(__file__).parent / "fixtures" / "xplorer"
URL = "https://www.xplorer.co.il/music/artist/38?ev=2325120"


def _page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def test_xplorer_artist_ids_from_board_filters() -> None:
    """Only the numeric filter values are artists (the others are cities, months, ticket counts)."""
    assert xplorer.artist_ids(_page("music")) == ["752", "1124", "867"]
    with pytest.raises(ParseError):
        xplorer.artist_ids("<html></html>")


def test_xplorer_artist_page_lists_every_show() -> None:
    ids = xplorer.event_ids(_page("artist_rieu"))
    assert len(ids) == 19 and "0" not in ids and ids[:2] == ["2319537", "2325120"]
    assert xplorer.performer(_page("artist_rieu")) == "אנדרה ריו"


def test_xplorer_single_show_artist_id_from_jsonld() -> None:
    """One show: only the "cheapest" row (id 0), so the id comes from the JSON-LD offer link."""
    assert xplorer.event_ids(_page("artist_single")) == ["2327751"]
    assert xplorer.performer(_page("artist_single")) == "FKA Twigs"


def test_xplorer_event_page() -> None:
    e = xplorer.parse_event(_page("event"), URL, "2325120", "Asia/Jerusalem", ["אנדרה ריו"])
    assert e is not None and e.source_event_id == "2325120" and e.title == "אנדרה ריו"
    assert e.start_at == datetime(2026, 10, 31, 20, tzinfo=ZoneInfo("Europe/Lisbon"))
    assert (e.venue_name, e.city, e.country) == ("ALTICE ARENA", "ליסבון", "PT")
    assert (e.currency, e.min_price, e.max_price, e.is_sold_out) == ("USD", Decimal(141), Decimal(220), False)
    assert (e.tickets[0].name, e.tickets[0].price, e.tickets[0].remaining) == ("טבעת עליונה, ישיבה", Decimal(141), 4)
    assert len(e.tickets) == 2 and e.image_url and "rieu" in e.image_url.lower()


def test_xplorer_event_artist_from_page_and_pulled_event() -> None:
    e = xplorer.parse_event(_page("event"), URL, "2325120", "Asia/Jerusalem")
    assert e is not None and e.artists == ["אנדרה ריו"]
    assert xplorer.parse_event("<html></html>", URL, "1", "Asia/Jerusalem") is None


def test_xplorer_festival_names_the_event() -> None:
    """A festival is an "artist" page of its own: it becomes the title, its acts the artists."""
    names = ["Fontaines D.C.", "פסטיבל פרימוורה", "FKA Twigs"]
    e = xplorer.parse_event(_page("event"), URL, "2325120", "Asia/Jerusalem", names)
    assert e is not None and e.title == "פסטיבל פרימוורה" and e.artists == ["Fontaines D.C.", "FKA Twigs"]
