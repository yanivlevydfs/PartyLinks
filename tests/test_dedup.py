"""Duplicate listings collapse into one primary event; different events never merge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upsert_events
from partylinks.dedup import Deduplicator
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent, TicketTier

START = datetime.now(UTC).replace(hour=19, minute=30, second=0, microsecond=0) + timedelta(days=3)
MOON = (32.1002, 34.7745)


def listing(source: str, eid: str, title: str, start: datetime = START, where: tuple[float, float] | None = MOON, **extra: Any) -> ScrapedEvent:
    fields: dict[str, Any] = {
        "source": source, "source_event_id": eid, "url": f"https://{source}.example/{eid}", "title": title,
        "start_at": start, "address": "Tel Aviv-Yafo, Israel", "latitude": where[0] if where else None,
        "longitude": where[1] if where else None, "tickets": [TicketTier(name="Entry", price=Decimal(80))],
    }  # fmt: skip
    return ScrapedEvent(**(fields | extra))


def grouped(tmp_path: Path, events: list[ScrapedEvent]) -> tuple[Settings, dict[str, tuple[str | None, bool]]]:
    settings = Settings(database={"url": f"sqlite:///{tmp_path / 'd.db'}"}, scheduler={"enabled": False})
    db = Database(settings.database.url)
    db.create_schema()
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        upsert_events(session, [e for e in map(enricher.enrich, events) if e])
    with db.session() as session:
        Deduplicator(settings.dedup).regroup(session)
    with db.session() as session:
        return settings, {r.id: (r.group_id, r.is_primary) for r in session.query(EventRow)}


def test_promoter_copies_and_cross_platform_listings_merge(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM | 22.10"),
        listing("goout", "b", "22.10 THURSDAY MOON | MAINSTREAM |"),
        listing("eventer", "c", "THURSDAY MOON | MAINSTREAM | 22.10 🌙", start=START + timedelta(minutes=30)),
        listing("tickchak", "d", "THURSDAY MOON", where=None, extra={"purchase_url": "https://goout.example/a"}),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1
    assert [eid for eid, (_, primary) in groups.items() if primary] == ["goout:a"]  # source priority, not tickchak


def test_different_events_stay_separate(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM"),
        listing("goout", "b", "THURSDAY 22.10 | PANDA TLV"),  # same place and time, different party
        listing("goout", "c", "THURSDAY MOON | MAINSTREAM", start=START + timedelta(hours=3)),  # later show
        listing("goout", "d", "THURSDAY MOON | MAINSTREAM", where=(32.05, 34.76)),  # another venue
        listing("goout", "e", "THURSDAY MOON | MAINSTREAM", start=START + timedelta(days=7)),  # next week
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 5 and all(primary for _, primary in groups.values())


def test_api_returns_one_event_with_alternatives(tmp_path: Path) -> None:
    settings, _ = grouped(tmp_path, [
        listing("goout", "a", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(90))]),
        listing("selector", "b", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(70))]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events").json()
        assert page["total"] == 1 and page["items"][0]["listings"] == 2
        detail = client.get(f"/events/{page['items'][0]['id']}").json()
        assert [(a["source"], a["min_price"]) for a in detail["alternatives"]] == [("selector", 70.0)]
        assert client.get("/events", params={"include_duplicates": True}).json()["total"] == 2


def test_free_or_cheaper_listing_on_another_source_is_never_hidden(tmp_path: Path) -> None:
    settings, groups = grouped(tmp_path, [
        listing("goout", "a", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(90))]),
        listing("selector", "b", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[
            TicketTier(name="רשומים חינם עד 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(70)),
        ]),
    ])  # fmt: skip
    assert groups["goout:a"][1] is True  # go-out stays the primary listing (source priority)...
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        free = client.get("/events", params={"free_entry": True}).json()  # ...but the free deal is found
        assert free["total"] == 1
        event = free["items"][0]
        assert (event["id"], event["group_has_free_entry"], event["group_entry_type"]) == (
            "goout:a", True, "free_and_paid",
        )  # fmt: skip
        assert (event["group_min_price"], event["best_deal"]["source"]) == (0.0, "selector")
        assert client.get("/events", params={"max_price": 50}).json()["total"] == 1
        detail = client.get("/events/goout:a").json()
        assert [t["kind"] for t in detail["alternatives"][0]["tickets"]] == ["free", "paid"]


def test_area_level_pin_merges_only_on_same_title_and_time(tmp_path: Path) -> None:
    port = (32.0962, 34.7732)  # an aggregator's "Tel Aviv Port" pin, ~460 m from the club
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "FRIDAY MAINSTREAM | 06.11"),
        listing("popit", "b", "FRIDAY MAINSTREAM | 06.11", where=port),
        listing("popit", "c", "SATURDAY TECHNO NIGHT", where=port),  # other party, same coarse pin
        listing("popit", "d", "FRIDAY MAINSTREAM | 06.11", start=START + timedelta(hours=2), where=port),
    ])  # fmt: skip
    assert groups["goout:a"][0] == groups["popit:b"][0]
    assert groups["popit:c"][0] != groups["goout:a"][0]
    assert groups["popit:d"][0] != groups["goout:a"][0]  # same title, different time


def test_happy_hours_of_neighbouring_bars_stay_apart(tmp_path: Path) -> None:
    bar = {"extra": {"happy_hour": True}, "source_genres": ["happy hour"]}
    _, groups = grouped(tmp_path, [
        listing("happytlv", "hh-a", "האפי האוור באהובה", where=(32.0800, 34.7740), venue_name="אהובה", **bar),
        listing("happytlv", "hh-b", "האפי האוור באורבנו", where=(32.0801, 34.7741), venue_name="אורבנו", **bar),
    ])  # fmt: skip
    assert groups["happytlv:hh-a"][0] != groups["happytlv:hh-b"][0]
