"""The database code behaves the same on SQLite and PostgreSQL. Runs on SQLite; with
PARTYLINKS_TEST_PG_URL set (see conftest.py) every test here runs on PostgreSQL too."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text

from partylinks.ads.models import SponsoredRow
from partylinks.config import get_settings
from partylinks.db import Database, EventRow, upsert_events, whole_word
from partylinks.dbcopy import copy_database
from partylinks.models import TicketTier
from tests.test_storage_api import event, seed, settings_for


def test_columns_added_to_an_old_table_get_their_default(tmp_path: Path) -> None:
    """A boolean / text / integer column added to an existing table: old rows take the model default
    (PostgreSQL refuses SQLite's DEFAULT 1 on a boolean)."""
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
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
    """SQLite's NULL order, kept on PostgreSQL (whose default is the opposite)."""
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


def test_copy_db_round_trip_is_verified(tmp_path: Path) -> None:
    """Database -> another -> a third: every table arrives and its checksum matches the source."""
    settings = settings_for(tmp_path)
    start = datetime.now(UTC) + timedelta(days=1)
    seed(settings, [event(str(i), start, genres=["techno"], extra={"k": [1, 2.5]}) for i in range(5)])
    first, second = f"sqlite:///{tmp_path / 'b.db'}", f"sqlite:///{tmp_path / 'c.db'}"
    options = get_settings().database
    on_postgres = Database(first).engine.dialect.name == "postgresql"  # conftest swaps SQLite URLs
    if on_postgres:  # one PostgreSQL database: copy it to SQLite and back into it
        first, second = f"sqlite+pysqlite:///{tmp_path / 'b.db'}", settings.database.url
    reports = copy_database(settings.database.url, first, options)
    assert all(r.ok for r in reports.values()) and reports["events"].rows == 5 and reports["ticket_tiers"].rows == 10
    reports = copy_database(first, second, options, force=True)
    assert all(r.ok for r in reports.values()) and reports["events"].rows == 5
    with Database(second).session() as session:  # the id sequences moved past the copied ids
        upsert_events(session, [event("new", start)])


def test_the_old_artist_file_is_imported_once(tmp_path: Path) -> None:
    import sqlite3

    from partylinks.artistdb import ArtistBook
    from partylinks.artists import ArtistProfile

    old = tmp_path / "artists.db"
    with sqlite3.connect(old) as conn:  # the file as the old ArtistBook wrote it
        conn.executescript("""
            CREATE TABLE artists (key TEXT PRIMARY KEY, name TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE profiles (key TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL, kind TEXT, genre TEXT,
                image_url TEXT, spotify_url TEXT, profile_url TEXT, links TEXT, updated_at TEXT NOT NULL);
            INSERT INTO artists VALUES ('millero', 'MILLERO', 3, 0, '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00');
            INSERT INTO artists VALUES ('open air', 'OPEN AIR', 0, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            INSERT INTO profiles VALUES ('dali', 'Dali', 'goout', 'dj', NULL, 'https://img/d.jpg', NULL, NULL,
                '["https://instagram.com/dali"]', '2026-01-01T00:00:00+00:00');
        """)
    db = Database(f"sqlite:///{tmp_path / 'main.db'}")
    db.create_schema()
    book = ArtistBook(db, old)
    assert book.load() == ({"millero", "dali"}, {"open air"})
    assert book.profiles()["dali"] == ArtistProfile("Dali", "goout", kind="dj", image_url="https://img/d.jpg",
                                                    links=["https://instagram.com/dali"])  # fmt: skip
    book.record(["Millero", "MILLERO", "Shalos"])
    ArtistBook(db, old)  # a later start: the tables are no longer empty, nothing is imported again
    assert dict((n, s) for n, s, _ in book.rows())["MILLERO"] == 5
