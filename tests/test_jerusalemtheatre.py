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


def test_box_office_price_levels() -> None:
    from partylinks.scrapers.jerusalemtheatre import apply_prices
    from partylinks.scrapers.pres import parse_ticket_groups

    groups = json.loads((Path(__file__).parent / "fixtures/jerusalemtheatre/ticket_groups.json").read_text("utf-8"))
    events = parse_board(BOARD, SETTINGS, TZ)
    free = next(e for e in events if e.min_price == 0)
    paid = [e for e in events if e.min_price is None]
    apply_prices([*paid, free], parse_ticket_groups(groups))
    # Two price levels at 42 (regular); the 34 senior discount is left out.
    assert all((e.min_price, e.max_price) == (Decimal(42), Decimal(42)) for e in paid)
    assert [(t.name, t.price) for t in paid[0].tickets] == [("רמה1", Decimal(42)), ("רמה2", Decimal(42))]
    assert free.min_price == 0 and [t.price for t in free.tickets] == [Decimal(0)]
    assert paid[0].extra["ticket"] == paid[0].source_event_id


def test_box_office_listing_adds_and_enriches() -> None:
    from partylinks.scrapers.jerusalemtheatre import add_presentations, parse_presentation

    events = parse_board(BOARD, SETTINGS, TZ)
    board = events[0]
    rows = [  # PRES /api/presentations/ rows (November 2026): one the board has, one it lacks
        {"id": int(board.source_event_id), "venueName": "עוזי וכסלר ", "durationInMinutes": 120, "soldout": 1},
        {
            "id": 35152, "dateTime": "2027-05-20 20:30", "featureId": 8406,
            "featureName": "להקת המחול הקיבוצית | בכורה 26-27 עולמית", "featureCategoryName": "מחול",
            "venueName": "תיאטרון שרובר", "durationInMinutes": 70, "soldout": 0,
        },
    ]  # fmt: skip
    [missing] = add_presentations(events, rows)
    assert board.is_sold_out and board.extra["hall"] == "עוזי וכסלר" and board.extra["duration_minutes"] == 120
    feature = {"id": 8406, "synopsis": "<p>בכורה עולמית</p>", "imageData": "https://cdn.presglobal.pro/f/1"}
    e = parse_presentation(missing, feature, SETTINGS, TZ)
    assert (e.source_event_id, str(e.url)) == ("35152", "https://jtheater.pres.global/order/35152")
    assert e.start_at is not None and e.start_at.isoformat() == "2027-05-20T20:30:00+03:00"
    assert (e.source_genres, e.description, e.images) == (["מחול"], "בכורה עולמית", ["https://cdn.presglobal.pro/f/1"])
    assert e.extra["ticket"] == "35152" and e.extra["show_id"] == "pres:8406" and e.venue_name == "תיאטרון ירושלים"
