"""Jerusalem Theatre: the board feed to one ScrapedEvent per performance."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.jerusalemtheatre import JerusalemTheatreSettings, parse_board

TZ = "Asia/Jerusalem"
BOARD = json.loads((Path(__file__).parent / "fixtures/jerusalemtheatre/board.json").read_text("utf-8"))
SETTINGS = JerusalemTheatreSettings(
    cron="0 6 * * *", site_url="https://www.jerusalem-theatre.co.il", board_path="/na_ajax.php",
    venue_name="תיאטרון ירושלים", address="דוד מרכוס 20", city="ירושלים",
)  # fmt: skip


def test_every_performance_with_wall_clock_time() -> None:
    events = {e.source_event_id: e for e in parse_board(BOARD, SETTINGS, TZ)}
    assert len(events) == 5  # 3 screenings, 1 play, 1 free concert
    film = events["35619"]
    # The site shows 18:00 on 21.10; the feed writes that clock reading as UTC.
    assert film.title == "האהבות שלנו" and film.start_at is not None
    assert film.start_at.isoformat() == "2026-10-21T18:00:00+03:00"
    assert (film.venue_name, film.city, film.source_genres) == ("תיאטרון ירושלים", "ירושלים", ["קולנוע"])
    assert film.images[0].startswith("https://www.jerusalem-theatre.co.il/prdPics/")
    assert film.min_price is None and not film.tickets  # prices live in the box office only


def test_free_show_and_skipped_genre() -> None:
    free = next(e for e in parse_board(BOARD, SETTINGS, TZ) if e.min_price == 0)
    assert free.source_event_id.startswith("4555:")  # no ticket id: show id + time
    assert [(t.price, t.is_free) for t in free.tickets] == [(Decimal(0), True)]
    no_cinema = SETTINGS.model_copy(update={"skip_groups": ["eventGrp_cinema"]})
    assert {e.title for e in parse_board(BOARD, no_cinema, TZ)} == {
        "יאסו – האהבה היוונית שלי",
        "סטודנטים מהאקדמיה למוסיקה ולמחול בירושלים: מחווה לזמרות ולזמרי הג'אז הגדולים של אמריקה",
    }
