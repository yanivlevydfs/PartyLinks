"""Radical parser test against the real calendar page and Store API products (November 2026)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import radical

FIXTURES = Path(__file__).parent / "fixtures" / "radical"
TZ = "Asia/Jerusalem"
TODAY = date(2026, 11, 16)


def _setup() -> tuple[radical.RadicalSettings, dict, dict]:
    cfg = radical.RadicalSettings.model_validate(Settings().sources["radical"])
    grids = radical.parse_calendar((FIXTURES / "calendar.html").read_text(encoding="utf-8"))
    products = {str(p["id"]): p for p in json.loads((FIXTURES / "products.json").read_text(encoding="utf-8"))}
    return cfg, grids, products


def test_radical_calendar_grids() -> None:
    _, grids, _ = _setup()
    main = grids["a8cdcc9"]  # all coming events, 12 a page, "load more" to page 2
    assert len(main["cards"]) == 12
    assert main["next"] == "https://radical.org.il/calendar/?e-page-a8cdcc9=2"
    archive = grids["717a4bf"]  # past events: never paged (its first page has no coming date)
    assert not any(radical.card_day(c["date"], TODAY) >= TODAY for c in archive["cards"])
    card = main["cards"][0]
    assert card["id"] == "81894" and card["url"] == "https://radical.org.il/product/psychedelics-and-humans/"
    assert card["date"] == "יום שני, 16/11" and card["time"] == "18:30"
    assert "lecture" in card["categories"]


def test_radical_card_day_year() -> None:
    assert radical.card_day("יום שני, 16/11", TODAY) == date(2026, 11, 16)
    # January in November is next year; its weekday confirms it (5/1/2027 is a Tuesday).
    assert radical.card_day("יום שלישי, 05/01", TODAY) == date(2027, 1, 5)
    assert radical.card_day("יום רביעי, 11/11", TODAY) == date(2026, 11, 11)
    assert radical.card_day("", TODAY) is None


def test_radical_events() -> None:
    cfg, grids, products = _setup()
    cards = {c["id"]: c for g in grids.values() for c in g["cards"]}

    talk = radical.parse_event(cards["81894"], products["81894"], cfg, TZ, TODAY)
    assert talk is not None
    assert talk.title == "האם פטריות פסיכדליות הפכו אותנו לבני אדם?"
    assert talk.start_at == datetime(2026, 11, 16, 18, 30, tzinfo=ZoneInfo(TZ))
    assert talk.venue_name == "בית רדיקל" and talk.city == "תל אביב-יפו"
    assert (talk.latitude, talk.longitude) == (cfg.latitude, cfg.longitude)
    assert talk.source_genres == ["הרצאה"]  # "אירועים" / "אודיטוריום" are skipped
    assert talk.extra == {
        "purchase_url": "https://radical.org.il/product/psychedelics-and-humans/",
        "hall": "האודיטוריום",
    }
    assert talk.min_price == Decimal(75) and talk.images[0].startswith("https://radical.org.il/wp-content/uploads/")
    assert talk.description
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    enriched = enricher.enrich(talk)
    assert enriched is not None and "lectures" in enriched.genres

    sauna = radical.parse_event(cards["82695"], products["82695"], cfg, TZ, TODAY)  # a price range, basement
    assert sauna is not None
    assert (sauna.min_price, sauna.max_price) == (Decimal(60), Decimal(70))
    assert sauna.source_genres == [] and sauna.extra["hall"] == "המרתף"
    enriched = enricher.enrich(sauna)
    assert enriched is not None and enriched.genres  # from its text, else the source default

    both = radical.parse_event(cards["71619"], products["71619"], cfg, TZ, TODAY)  # in both halls' lists
    assert both is not None and both.extra["hall"] is None and both.source_genres == ["הופעה"]

    party = radical.parse_event(cards["88640"], products["88640"], cfg, TZ, TODAY)  # no time, free, full
    assert party is not None
    assert party.time_known is False and party.start_at == datetime(2026, 11, 21, tzinfo=ZoneInfo(TZ))
    assert party.is_sold_out and party.tickets[0].is_free

    # No Store API product: the card alone still makes an event (its classes carry slugs, not names).
    bare = radical.parse_event(cards["81894"], None, cfg, TZ, TODAY)
    assert bare is not None and bare.min_price is None and not bare.tickets and bare.source_genres == []
    assert radical.parse_event({**cards["81894"], "date": ""}, None, cfg, TZ, TODAY) is None
