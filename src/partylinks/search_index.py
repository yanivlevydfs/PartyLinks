"""The search's full-text index: a tsvector of each event's names (title, lineup, venue), places and kinds
(city, address, organizer, the site's categories) and the start of its description, stored in
``events.search_v1`` and indexed with GIN. Text is normalised the way the search words are (``norm``):
case, Latin accents, Hebrew final letters (ך כ, ם מ, ן נ, ף פ, ץ צ), niqqud, geresh / gershayim and quotes,
punctuation. A word matches as the start of a word in the text ("עומ" finds "עומר"), maybe behind Hebrew
prefix letters ("בארבי" finds "בבארבי"), from the index instead of an ILIKE over every row (prod: 1.5-6 s).

Built-in PostgreSQL only (no extension). The column is filled by a trigger on insert and on a change of
the columns it reads (no Python code to keep in step), and once for the rows already stored, in batches in
the background, before the index is built CONCURRENTLY: a deploy neither waits for it nor blocks writes.
Stored, not an expression index: the index's recheck of a lossy bitmap (a search matching tens of
thousands of rows) reads the stored vector instead of recomputing every row's (measured: 40+ s).
Until it is all in place, ``ready()`` is False and the search keeps its plain ILIKE matching.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from sqlalchemy import ColumnElement, Engine, func, literal_column, text
from sqlalchemy.exc import DBAPIError

log = logging.getLogger(__name__)

# Bump the version when the vector's recipe changes: a new column, trigger and index (the old ones dropped).
VERSION = 1
COLUMN = f"search_v{VERSION}"
FUNCTION = f"spotx_search_v{VERSION}"
INDEX = f"ix_events_search_v{VERSION}"
GENRES_INDEX = "ix_events_genres_gin"
LOCK_KEY = 0x5EA2C4  # pg_try_advisory_lock: one process fills and builds
SYNC_BUILD_ROWS = 20_000  # a table this small (tests, a fresh database) is done at startup, synchronously
BATCH = 2000  # rows per backfill UPDATE
DESCRIPTION_CHARS = 2000  # the start of a description: long texts cost index size, not matches people expect

_ACCENTS = "ÀÁÂÃÄÅàáâãäåÈÉÊËèéêëÌÍÎÏìíîïÒÓÔÕÖØòóôõöøÙÚÛÜùúûüÑñÇçÝýÿ"
_PLAIN = "aaaaaaaaaaaaeeeeeeeeiiiiiiiioooooooooooouuuuuuuunnccyyy"
_FINALS, _BASES = "ךםןףץ", "כמנפצ"
# Word separators, each to a space: whitespace, ASCII punctuation, common Unicode punctuation and symbols.
_PUNCT = (
    "\t\n\r\u00a0\u200b\u200e\u200f!#$%&()*+,-./:;<=>?@[\\]^_{|}~–—―‐‑‒…«»•·‚„‹›¡¿§¶©®™°±×÷־׀׃׆|¦¨¬¯¸¤¢£¥€₪→←↑↓★☆♥❤✨🔥"
)
# Deleted, not spaced: niqqud and cantillation, geresh / gershayim, quotes and apostrophes ("ג'אז" is "גאז").
_DROP = "".join(chr(c) for c in (*range(0x591, 0x5BE), 0x5BF, 0x5C1, 0x5C2, 0x5C4, 0x5C5, 0x5C7)) + "׳״'\"`´‘’“”"
_TABLE = str.maketrans(_ACCENTS + _FINALS + _PUNCT, _PLAIN + _BASES + " " * len(_PUNCT), _DROP)
HE_PREFIXES = ("ב", "ל", "מ", "ה", "ו", "כ", "ש", "וה", "וב", "ול", "ומ", "וכ", "שה", "שב", "של", "מה", "כש", "לה", "בה")  # fmt: skip
_HEBREW = re.compile(r"[֐-׿]")


def norm(value: str | None) -> str:
    """``value`` as the index holds it, words split by single spaces. The SQL ``spotx_norm`` maps the same
    characters (it leaves runs of spaces; the case of non-ASCII letters is the database's ``lower``)."""
    lowered = "".join(c.lower() if c < "\x80" else c for c in (value or ""))
    return " ".join(lowered.translate(_TABLE).split())


def _sql_string(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_SOURCES = "title, venue_name, lineup, city, address, organizer, event_types, source_genres, description"
DDL = [
    # translate() only (no regexp): ranking runs it on each match, ~1 µs.
    (  # one statement, split over lines
        "CREATE OR REPLACE FUNCTION spotx_norm(t text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT "
        f"translate(lower(coalesce(t, '')), {_sql_string(_ACCENTS + _FINALS + _PUNCT + _DROP)}, "
        f"{_sql_string(_PLAIN + _BASES + ' ' * len(_PUNCT))}) $$"
    ),
    f"""CREATE OR REPLACE FUNCTION {FUNCTION}(title text, venue text, lineup jsonb, city text, address text,
        organizer text, types jsonb, genres jsonb, description text) RETURNS tsvector
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
        SELECT strip(to_tsvector('simple'::regconfig, spotx_norm(coalesce(title, '') || ' ' || coalesce(lineup::text, '')
            || ' ' || coalesce(venue, '') || ' ' || coalesce(city, '') || ' ' || coalesce(address, '') || ' '
            || coalesce(organizer, '') || ' ' || coalesce(types::text, '') || ' ' || coalesce(genres::text, '') || ' '
            || left(coalesce(description, ''), {DESCRIPTION_CHARS}))))
    $$""",
    # Recomputed only when a column it reads changes: a rescan that only marks an event seen costs nothing.
    f"""CREATE OR REPLACE FUNCTION {FUNCTION}_fill() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' OR NEW.{COLUMN} IS NULL
            OR (NEW.title, NEW.venue_name, NEW.lineup, NEW.city, NEW.address, NEW.organizer, NEW.event_types,
                NEW.source_genres, NEW.description)
               IS DISTINCT FROM (OLD.title, OLD.venue_name, OLD.lineup, OLD.city, OLD.address, OLD.organizer,
                OLD.event_types, OLD.source_genres, OLD.description) THEN
            NEW.{COLUMN} := {FUNCTION}(NEW.title, NEW.venue_name, NEW.lineup, NEW.city, NEW.address, NEW.organizer,
                NEW.event_types, NEW.source_genres, NEW.description);
        END IF;
        RETURN NEW;
    END $$""",
    # No default: no rewrite. Only when missing (an ALTER takes the table's lock even when it changes nothing).
    f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'events'::regclass AND attname = '{COLUMN}' AND NOT attisdropped) THEN
            SET LOCAL lock_timeout = '10s';
            ALTER TABLE events ADD COLUMN {COLUMN} tsvector;
        END IF;
    END $$""",
    f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = '{FUNCTION}_trg' AND tgrelid = 'events'::regclass) THEN
            SET LOCAL lock_timeout = '10s';
            CREATE TRIGGER {FUNCTION}_trg BEFORE INSERT OR UPDATE ON events FOR EACH ROW EXECUTE FUNCTION {FUNCTION}_fill();
        END IF;
    END $$""",
]
INDEXES = {
    INDEX: f"ON events USING gin ({COLUMN})",
    GENRES_INDEX: "ON events USING gin (genres jsonb_path_ops)",
}

_ready = threading.Event()


def ready() -> bool:
    """The vectors are filled and the indexes valid: the search may use ``matches``."""
    return _ready.is_set()


def vector() -> ColumnElement[Any]:
    return literal_column(f"events.{COLUMN}")


def _lexemes(word: str, prefix: bool) -> list[str]:
    """tsquery operands for one normalised word: itself and, for a Hebrew word, behind prefix letters."""
    forms = [word, *(p + word for p in HE_PREFIXES)] if _HEBREW.match(word) else [word]
    return [f"{_sql_string(f)}{':*' if prefix else ''}" for f in forms]


def query_text(words: list[str], prefix: bool = True) -> str | None:
    """A ``to_tsquery`` string: every word (any of its forms). None: no word left."""
    parts = [" | ".join(_lexemes(w, prefix)) for w in words if w]
    return " & ".join(f"({p})" for p in parts) or None


def matches(phrase: str, prefix: bool = True) -> ColumnElement[bool]:
    """Events whose text has every word of ``phrase`` (as a word's start; ``prefix=False``: whole words)."""
    q = query_text(norm(phrase).split(), prefix)
    if q is None:
        return literal_column("false")
    return vector().op("@@")(func.to_tsquery(literal_column("'simple'::regconfig"), q))


def ensure(engine: Engine) -> None:
    """Create the functions, column and trigger; then fill the column and build the indexes, at once on a
    small table, else in a background thread. Never raises: the search works without them."""
    try:
        with engine.begin() as conn:
            for ddl in DDL:
                conn.execute(text(ddl))
            for old in conn.scalars(  # an earlier version's column (its index and trigger go with it)
                text(
                    "SELECT attname FROM pg_attribute WHERE attrelid = 'events'::regclass AND attname ~ '^search_v[0-9]+$' "
                    "AND attname <> :c AND NOT attisdropped"
                ),
                {"c": COLUMN},
            ).all():
                conn.execute(text(f"ALTER TABLE events DROP COLUMN IF EXISTS {old}"))
            rows = conn.scalar(text("SELECT reltuples FROM pg_class WHERE oid = 'events'::regclass")) or 0
            count = conn.scalar(text("SELECT count(*) FROM (SELECT 1 FROM events LIMIT :n) t"), {"n": SYNC_BUILD_ROWS})
    except DBAPIError as exc:
        log.error("search index: not set up: %s", exc.orig)
        return
    if _done(engine):
        _ready.set()
        return
    _ready.clear()
    if max(rows, count or 0) < SYNC_BUILD_ROWS:
        _build(engine, concurrently=False)
        return
    threading.Thread(target=_build, args=(engine, True), name="search-index", daemon=True).start()


def _done(engine: Engine) -> bool:
    with engine.connect() as conn:
        valid = conn.scalar(
            text(
                "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = ANY(:names) AND i.indisvalid"
            ),
            {"names": list(INDEXES)},
        )
        return valid == len(INDEXES) and not conn.scalar(
            text(f"SELECT EXISTS (SELECT 1 FROM events WHERE {COLUMN} IS NULL)")
        )


def _fill(conn: Any, pause: float) -> int:
    """Vectors for the rows stored before the trigger, by primary key, ``BATCH`` rows an UPDATE."""
    last, filled = "", 0
    while ids := conn.scalars(
        text("SELECT id FROM events WHERE id > :last ORDER BY id LIMIT :n"), {"last": last, "n": BATCH}
    ).all():
        last = ids[-1]
        for attempt in range(5):  # a scrape's upsert of the same rows can deadlock with it: that batch again
            try:
                result = conn.execute(
                    text(
                        f"UPDATE events SET {COLUMN} = {FUNCTION}({_SOURCES}) WHERE id = ANY(:ids) AND {COLUMN} IS NULL"
                    ),
                    {"ids": sorted(ids)},
                )
                filled += result.rowcount or 0
                break
            except DBAPIError:
                if attempt == 4:
                    raise
                time.sleep(5)
        time.sleep(pause)  # the site and the scrapers write meanwhile
    return filled


def _build(engine: Engine, concurrently: bool) -> None:
    """Fill the column, then build what is missing (an invalid index left by a failed build is dropped
    first), under an advisory lock; a process that finds the lock taken waits for the other to finish."""
    started = time.monotonic()
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if not conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}):
                while not _done(engine):  # another process builds them
                    time.sleep(30 if concurrently else 1)
                _ready.set()
                return
            try:
                conn.execute(
                    text("SET statement_timeout = 0")
                )  # minutes on a big table (the 2-minute cap is for queries)
                filled = _fill(conn, 0.2 if concurrently else 0)
                if filled:
                    log.info(
                        "search index: %s filled for %d events in %.0f s", COLUMN, filled, time.monotonic() - started
                    )
                how = "CONCURRENTLY " if concurrently else ""
                for name, spec in INDEXES.items():
                    state = conn.scalar(
                        text(
                            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"
                        ),
                        {"n": name},
                    )
                    if state is True:
                        continue
                    if state is False:
                        conn.execute(text(f"DROP INDEX {how}IF EXISTS {name}"))
                    conn.execute(text(f"CREATE INDEX {how}IF NOT EXISTS {name} {spec}"))
                    log.info("search index: %s built, %.0f s in", name, time.monotonic() - started)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    except DBAPIError as exc:  # the search stays on its ILIKE matching; retried at the next start
        log.error("search index: not built: %s", exc.orig)
        return
    if _done(engine):
        _ready.set()
