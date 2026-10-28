"""Copy the whole database to another one and prove the copy.

    partylinks copy-db --to postgresql://...                    # the configured DB -> PostgreSQL
    partylinks copy-db --from sqlite:///data/snap.db --to ...   # any URL to any URL (also back, for a rollback)

Every table goes through the same SQLAlchemy models on both sides, so dates, JSON and decimals are
written by the code that reads them. Rows the target would refuse are repaired on the way, and
counted: NUL characters (PostgreSQL text cannot hold them), NULL in a NOT NULL column (columns SQLite
added with ALTER TABLE) and text over a VARCHAR(n). Then each table is compared: row count, and a
checksum of every (repaired) source row against every target row, independent of row order.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, String, Table, func, insert, inspect, select, text
from sqlalchemy.engine import Connection

from partylinks.config import DatabaseSettings
from partylinks.db import Base, Database
from partylinks.models import without_nul

log = logging.getLogger(__name__)


class CopyRefused(RuntimeError):
    """The target already holds events: copying would overwrite a live database."""


@dataclass
class TableReport:
    rows: int = 0
    repaired: Counter[str] = field(default_factory=Counter)  # "nul" / "null" / "length" -> rows
    ok: bool = True
    problem: str = ""


def copy_database(
    source_url: str, target_url: str, options: DatabaseSettings, *, force: bool = False
) -> dict[str, TableReport]:
    """Copy every table of ``source_url`` into ``target_url`` and verify it. ``force`` drops the target's
    tables first; without it a target that already has events is refused."""
    source, target = Database(source_url, options=options), Database(target_url, options=options)
    try:
        _check_target(target, force)
        target.create_schema()
        present = inspect(source.engine)
        reports: dict[str, TableReport] = {}
        for table in Base.metadata.sorted_tables:  # parents before children (foreign keys)
            if not present.has_table(table.name):
                log.info("%s: not in the source, left empty", table.name)
                reports[table.name] = TableReport()
                continue
            columns = {c["name"] for c in present.get_columns(table.name)}
            reports[table.name] = _copy_table(source, target, table, columns, options.copy_batch)
        if not target.is_sqlite:
            _after_bulk_load(target)
        for table in Base.metadata.sorted_tables:
            if present.has_table(table.name):
                _verify(source, target, table, {c["name"] for c in present.get_columns(table.name)}, reports[table.name])
        return reports
    finally:
        source.engine.dispose()
        target.engine.dispose()


def _check_target(target: Database, force: bool) -> None:
    tables = Base.metadata.sorted_tables
    if force:
        log.warning("copy-db --force: dropping every table of the target first")
        Base.metadata.drop_all(target.engine)
        return
    if not inspect(target.engine).has_table("events"):
        return
    with target.engine.connect() as conn:
        busy = [t.name for t in tables if inspect(conn).has_table(t.name) and conn.scalar(select(func.count()).select_from(t))]
    if busy:
        raise CopyRefused(f"the target already has rows in {', '.join(busy)}; --force replaces them")


def _copy_table(source: Database, target: Database, table: Table, present: set[str], batch: int) -> TableReport:
    report = TableReport()
    with source.engine.connect() as read, target.engine.begin() as write:
        for rows in _batches(read, table, present, batch):
            write.execute(insert(table), [_repaired(table, row, report.repaired) for row in rows])
            report.rows += len(rows)
    fixes = ", ".join(f"{n} {kind}" for kind, n in report.repaired.items())
    log.info("%-16s %8d rows%s", table.name, report.rows, f"  (repaired: {fixes})" if fixes else "")
    return report


def _batches(conn: Connection, table: Table, present: set[str], batch: int) -> Iterator[list[dict[str, Any]]]:
    """The table's rows (only columns the source has), in primary key order, ``batch`` at a time."""
    query = select(*(c for c in table.columns if c.name in present)).order_by(*table.primary_key.columns)
    result = conn.execution_options(stream_results=True, yield_per=batch).execute(query)
    for part in result.partitions():
        yield [dict(row._mapping) for row in part]


def _repaired(table: Table, row: dict[str, Any], repaired: Counter[str]) -> dict[str, Any]:
    """``row`` as the target accepts it (see the module docstring); ``repaired`` counts each kind of fix."""
    out = {}
    for column in table.columns:
        value = row.get(column.name)
        clean = without_nul(value)
        if clean != value:
            repaired["nul"] += 1
        if clean is None and not column.nullable and column.default is not None and not column.primary_key:
            arg = column.default.arg  # type: ignore[attr-defined]
            clean = arg(None) if callable(arg) else arg
            repaired["null"] += 1
        length = getattr(column.type, "length", None)
        if isinstance(column.type, String) and length and isinstance(clean, str) and len(clean) > length:
            clean = clean[:length]
            repaired["length"] += 1
        if column.name in row or clean is not None:
            out[column.name] = clean
    return out


def _after_bulk_load(target: Database) -> None:
    """PostgreSQL: move every id sequence past the copied ids, then refresh the planner's statistics
    (without them it plans the first queries blind)."""
    with target.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not isinstance(pk[0].type, Integer) or pk[0].autoincrement is False:
                continue
            sequence = conn.scalar(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": pk[0].name})
            if sequence:
                top = conn.scalar(select(func.max(pk[0])))
                conn.execute(select(func.setval(sequence, (top or 0) + 1, False)))
        conn.execute(text("ANALYZE"))


def _verify(source: Database, target: Database, table: Table, present: set[str], report: TableReport) -> None:
    with source.engine.connect() as read:
        expected = _fingerprint(_repaired(table, r, Counter()) for rows in _batches(read, table, present, 5000) for r in rows)
    with target.engine.connect() as read:
        found = _fingerprint(r for rows in _batches(read, table, present, 5000) for r in rows)
    if expected != found:
        report.ok = False
        report.problem = f"source {expected[0]} rows / {expected[1][:12]}, target {found[0]} rows / {found[1][:12]}"
        log.error("%s: copy differs: %s", table.name, report.problem)


def _fingerprint(rows: Iterator[dict[str, Any]]) -> tuple[int, str]:
    """(row count, sha256 over the sorted row digests): the same on both databases, whatever their
    collation or the order rows come back in."""
    digests = sorted(hashlib.sha256(json.dumps(_canonical(row), sort_keys=True).encode()).digest() for row in rows)
    return len(digests), hashlib.sha256(b"".join(digests)).hexdigest()


def _canonical(value: Any) -> Any:
    """One spelling per value: 80 and 80.0 alike (JSONB keeps a number's scale), decimals normalised,
    datetimes as ISO, bytes by their hash."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float | Decimal):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return repr(value)
