"""Database guarantees: schema evolution, text PostgreSQL would refuse, whole-word search, NULL order,
the artist dictionary's upserts."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text

from partylinks.ads.models import SponsoredRow
from partylinks.artistdb import ArtistBook
from partylinks.artists import ArtistProfile
from partylinks.db import Database, EventRow, whole_word
from partylinks.models import TicketTier
from tests.test_storage_api import event, seed, settings_for


def test_columns_added_to_an_old_table_get_their_default(tmp_path: Path) -> None:
    """A boolean / text / integer column added to an existing table: old rows take the model default
    (a boolean default is written `true`, never `1`)."""
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    with db.engine.begin() as conn:  # the tables as they were before these columns existed
        conn.execute(text("CREATE TABLE sponsored (event_id TEXT PRIMARY KEY, side VARCHAR(8), position INTEGER, until TIMESTAMP, created_at TIMESTAMP)"))
        conn.execute(text("INSERT INTO sponsored VALUES ('test:a', 'right', 0, NULL, '2026-01-01 00:00:00')"))
        conn.execute(text("CREATE TABLE events (id TEXT PRIMARY KEY, source VARCHAR(32), source_event_id TEXT, url TEXT, title TEXT)"))
        conn.execute(text("INSERT INTO events VALUES ('test:old', 'test', 'old', 'https://e.example/old', 'Old')"))
    db.create_schema()
    with db.session() as session:
        assert session.get(SponsoredRow, "test:a").placement == "rails"  # type: ignore[union-attr]
        old = session.get(EventRow, "test:old")
        assert old is not None and old.time_known is True and old.is_listed is True and old.currency == "ILS"
    db.create_schema()  # a second start changes nothing


def test_a_long_id_and_a_nul_character_never_cost_an_event(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings, [event("x" * 300, start, title="Night\x00 out", city="Tel Aviv " * 30)])
    with db.session(write=False) as session:
        row = session.scalars(select(EventRow)).one()
    assert row.source_event_id == "x" * 300 and row.title == "Night out"


def test_short_words_match_whole_words_only(tmp_path: Path) -> None:
    start = datetime.now(UTC) + timedelta(days=1)
    db = seed(settings_for(tmp_path), [
        event("rap", start, title="ערב ראפ בבארבי"), event("prefix", start, title="היפהופ בראפ"),
        event("therapy", start, title="סאונד תראפיה"), event("dot", start, title="a.b party"),
    ])  # fmt: skip
    with db.session(write=False) as session:
        found = set(session.scalars(select(EventRow.source_event_id).where(whole_word(EventRow.title, "ראפ"))))
        dotted = set(session.scalars(select(EventRow.source_event_id).where(whole_word(EventRow.title, "a.b"))))
        wildcard = set(session.scalars(select(EventRow.source_event_id).where(whole_word(EventRow.title, "a?b"))))
    assert found == {"rap", "prefix"}  # not inside "תראפיה"
    assert dotted == {"dot"} and wildcard == set()  # the word is text, never a pattern


def test_events_without_a_price_sort_first_ascending_and_last_descending(tmp_path: Path) -> None:
    """A missing price sorts as the smallest value (PostgreSQL's own default is the opposite)."""
    from fastapi.testclient import TestClient

    from partylinks.api import create_app

    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    seed(settings, [
        event("cheap", start, tickets=[TicketTier(name="Entry", price=Decimal(20))]),
        event("dear", start, tickets=[TicketTier(name="Entry", price=Decimal(90))]),
        event("unknown", start, tickets=[]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        order = {s: [e["id"] for e in client.get(f"/events?sort={s}").json()["items"]] for s in ("min_price", "-min_price")}
    assert order["min_price"] == ["test:unknown", "test:cheap", "test:dear"]
    assert order["-min_price"] == ["test:dear", "test:cheap", "test:unknown"]


def test_the_artist_dictionary_counts_and_merges(tmp_path: Path) -> None:
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    book = ArtistBook(db)
    book.record(["Millero", "MILLERO", "Shalos"])  # one name in two spellings: one row, counted twice
    book.record(["millero"])
    book.set("OPEN AIR", blocked=True)
    book.save_profiles([ArtistProfile("Dali", "goout", image_url="https://img/d.jpg", links=["https://instagram.com/dali"])])
    book.save_profiles([ArtistProfile("DALI", "eventer", kind="dj")])  # sparser: keeps the photo and links
    assert book.load() == ({"millero", "shalos", "dali"}, {"open air"})
    assert {n: s for n, s, _ in book.rows()}["Millero"] == 3
    assert book.profiles()["dali"] == ArtistProfile("DALI", "eventer", kind="dj", image_url="https://img/d.jpg",
                                                    links=["https://instagram.com/dali"])  # fmt: skip


def test_a_rescan_writes_only_the_ticket_tiers_that_changed(tmp_path: Path) -> None:
    """Unchanged tiers keep their row version (no dead row per scan), a changed one is updated,
    a tier gone from the site is deleted. An extension the server lacks (pgserver has no
    pg_stat_statements) is skipped, never fatal."""
    from partylinks.db import TicketRow

    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    tiers = [TicketTier(name="Early", price=Decimal(50)), TicketTier(name="Entry", price=Decimal(80)),
             TicketTier(name="Door", price=Decimal(100))]  # fmt: skip
    db = seed(settings, [event("a", start, tickets=tiers)])

    def versions() -> dict[int, tuple[str, str, Decimal | None]]:
        with db.session(write=False) as session:
            rows = session.execute(select(TicketRow.position, text("ticket_tiers.xmin::text"), TicketRow.name, TicketRow.price))
            return {r[0]: (r[1], r[2], r[3]) for r in rows}

    before = versions()
    seed(settings, [event("a", start, tickets=tiers)])
    assert versions() == before  # same scan again: nothing written
    seed(settings, [event("a", start, tickets=[tiers[0], TicketTier(name="Entry", price=Decimal(90))])])
    after = versions()
    assert after[0] == before[0] and after[1][1:] == ("Entry", Decimal(90)) and after[1][0] != before[1][0]
    assert 2 not in after
    with db.session(write=False) as session:
        options = session.scalar(text("SELECT reloptions FROM pg_class WHERE oid = 'ticket_tiers'::regclass"))
    assert f"fillfactor={settings.database.ticket_tiers_fillfactor}" in options
