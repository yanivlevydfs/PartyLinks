"""iTravelJerusalem: its events list (__NEXT_DATA__) and event pages' data, trimmed real answers (28.11.2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import next_data
from partylinks.scrapers.itraveljerusalem import Card, ITravelJerusalemSettings, card_of, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "itraveljerusalem"
TZ = "Asia/Jerusalem"
SETTINGS = ITravelJerusalemSettings(cron="30 10,22 * * *")


def _events() -> dict[str, dict[str, Any]]:
    return json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))


def _cards() -> list[Card]:
    props = next_data((FIXTURES / "list.html").read_text(encoding="utf-8"))["props"]["pageProps"]
    return [c for c in (card_of(raw, "he") for raw in props["listingCards"]) if c]


def test_list_cards() -> None:
    cards = {c.slug: c for c in _cards()}
    santa = cards["santas-house-experience-the-magic-of-christmas"]
    assert santa.id == 298779 and santa.latitude == 31.7779149
    assert cards["hanukkah-of-journeys"].latitude is None


def test_every_date_in_israel_time_with_its_end() -> None:
    card = Card(id=299069, slug="hanukkah-of-journeys", locale="he", labels=["חנוכה", "הצגות"])
    events = parse_event(_events()["hanukkah-of-journeys"], card, SETTINGS, TZ)
    assert [e.start_at.isoformat() for e in events if e.start_at] == [
        "2026-12-06T16:00:00+02:00",
        "2026-12-07T16:00:00+02:00",
        "2026-12-08T16:00:00+02:00",
    ]
    first = events[0]
    assert first.end_at is not None and first.end_at.isoformat() == "2026-12-06T18:30:00+02:00"
    assert first.title == "חנוכה של מסעות – תיאטרון הקרון" and first.city == "ירושלים"
    assert first.venue_name == "גן פעמון הדרור" and first.address == "גן פעמון הדרור, ירושלים"
    assert first.tickets[0].price == Decimal(45)
    assert first.extra["purchase_url"] == "https://traintheater.co.il/he/schedule/hanukkah"
    assert first.url.unicode_string() == "https://www.itraveljerusalem.com/he/event/hanukkah-of-journeys"
    assert first.source_event_id == "jo5y9zg9eh0gzgw3mogsxz92-20261206T1600"
    assert first.images[0] == "https://assets.itraveljerusalem.com/_a7ca81a8a6.jpg"
    assert first.description and "מרקו פולו" in first.description  # the notes too
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None and "hanukkah" in enriched.genres


def test_christmas_tree_lighting_and_santas_house() -> None:
    tree = parse_event(
        _events()["ymca-christmas-tree-1"], Card(id=299021, slug="ymca-christmas-tree-1", locale="he"), SETTINGS, TZ
    )
    assert (
        len(tree) == 1 and tree[0].start_at is not None and tree[0].start_at.isoformat() == "2026-11-29T17:00:00+02:00"
    )
    assert tree[0].extra == {} and tree[0].tickets == []  # no link, no price
    enriched = Enricher(Settings().enrichment, TZ).enrich(tree[0])
    assert enriched is not None and "christmas" in enriched.genres
    santa_card = Card(
        id=298779, slug="santas-house-experience-the-magic-of-christmas", locale="he", latitude=1.0, longitude=2.0
    )
    (santa,) = parse_event(_events()["santas-house-experience-the-magic-of-christmas"], santa_card, SETTINGS, TZ)
    assert santa.latitude == 31.7779149  # the page's pin before the card's
    assert santa.extra["purchase_url"] == "https://jerusalem-santa.com/"


def test_english_page_url_has_no_prefix() -> None:
    event = _events()["hanukkah-of-journeys"]
    (first, *_) = parse_event(event, Card(id=299084, slug="hanukkah-of-journeys", locale="en"), SETTINGS, TZ)
    assert first.url.unicode_string() == "https://www.itraveljerusalem.com/event/hanukkah-of-journeys"
