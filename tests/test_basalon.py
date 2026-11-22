"""Basalon parser tests on a real category page's cards and event pages' JSON-LD (November 2026)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import basalon

FIXTURES = Path(__file__).parent / "fixtures" / "basalon"
TZ = "Asia/Jerusalem"
CFG = basalon.BasalonSettings.model_validate(Settings().sources["basalon"])
TODAY = date(2026, 11, 22)


def page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def card(post_id: str, labels: list[str]) -> basalon.Card:
    return basalon.Card(post_id, f"https://basalon.co.il/event/{post_id}/", "x", TODAY, labels)


def test_cards_dates_and_appointment_only() -> None:
    cards = {c.post_id: c for c in basalon.parse_cards(page("lectures"), CFG, TODAY)}
    assert len(cards) == 6
    talk = cards["1487277"]
    assert talk.title.startswith("אוזן קשבת – 100 שנים למיילס דייויס")
    assert talk.url.startswith("https://basalon.co.il/event/")
    assert talk.when == date(2026, 11, 27)  # "יום ו׳, 27.11 ב- 12:45"
    assert cards["1336386"].when == date(2026, 11, 23)  # "מחר ב- 19:00"
    assert cards["1471866"].when == TODAY  # "היום ב- 20:00"
    assert cards["1459647"].when == date(2027, 1, 15)  # "יום ו׳, 15.1.27 ב- 13:00"
    # Booked by appointment / at the customer's home: no date on the card, not read further.
    assert cards["1342027"].when is None and cards["1485243"].when is None


def test_card_date_rolls_into_next_year() -> None:
    assert basalon.card_date("יום ב׳, 4.1 ב- 20:00", CFG, TODAY) == date(2027, 1, 4)
    assert basalon.card_date("בתיאום מראש", CFG, TODAY) is None
    assert basalon.card_date(None, CFG, TODAY) is None


def test_show_at_a_pub() -> None:
    show = basalon.parse_event(page("1487234"), card("1487234", ["הופעות"]), CFG, TZ)
    assert show is not None
    assert show.source_event_id == "1487234:2026-11-24"
    assert show.title == "אפשר להגזים? | רוק עברי מקורי"
    assert show.start_at == datetime(2026, 11, 24, 21, 0, tzinfo=ZoneInfo(TZ))
    assert show.end_at == datetime(2026, 11, 24, 23, 0, tzinfo=ZoneInfo(TZ))
    assert (show.venue_name, show.address, show.city) == ("דרך יפו 35, חיפה", "דרך יפו 35, חיפה", "חיפה")
    assert show.organizer == "Elis Pub"
    assert [(t.price, t.is_free) for t in show.tickets] == [(0, True)] and show.min_price == 0
    assert show.images and show.images[0].startswith("https://basalon.co.il/wp-content/uploads/")
    assert show.source_genres == ["הופעות"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and enriched.genres


def test_paid_show_tiers_and_genres() -> None:
    show = basalon.parse_event(page("1426935"), card("1426935", ["הופעות", "הרצאות"]), CFG, TZ)
    assert show is not None and show.min_price == 60 and show.max_price == 60
    assert show.address == "הנגר 8, הוד השרון" and show.city == "הוד השרון"
    assert show.title == "סטרלינג אוסטין – הבריטים באים!" and show.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and enriched.genres


def test_private_workshop_and_hidden_address_are_dropped() -> None:
    assert basalon.parse_event(page("1052068"), card("1052068", ["סדנאות"]), CFG, TZ) is None  # "סדנה פרטית לזוג"
    assert basalon.parse_event(page("1273829"), card("1273829", ["הרצאות"]), CFG, TZ) is None  # a locality only
    assert basalon.parse_event("<html></html>", card("984978", ["סדנאות"]), CFG, TZ) is None  # a gift card
