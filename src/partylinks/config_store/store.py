"""Read, change and apply the overrides (see the package docstring)."""

from __future__ import annotations

import copy
import logging
from typing import Any

import yaml
from sqlalchemy import delete, select

from partylinks.config import Settings, config_path, get_settings
from partylinks.config_store.models import ConfigOverrideRow
from partylinks.config_store.source import active, set_active
from partylinks.db import Database

log = logging.getLogger(__name__)

KEEP_VERSIONS = 50


def latest(db: Database) -> dict[str, Any]:
    with db.session(write=False) as session:
        row = session.scalars(select(ConfigOverrideRow).order_by(ConfigOverrideRow.id.desc()).limit(1)).first()
        return dict(row.data or {}) if row else {}


def load(db: Database) -> Settings:
    """Put the stored overrides in force in this process and return the settings rebuilt with them.
    A database that cannot be read leaves the file's settings in force (logged), never a crash at startup."""
    try:
        set_active(latest(db))
    except Exception:
        log.exception("config overrides: not read from the database, using settings.yaml alone")
    get_settings.cache_clear()
    return get_settings()


def save(db: Database, data: dict[str, Any], note: str | None = None) -> int:
    """Store a new version (the newest is in force) and keep the last KEEP_VERSIONS."""
    with db.session() as session:
        row = ConfigOverrideRow(data=data, note=note)
        session.add(row)
        session.flush()
        keep = select(ConfigOverrideRow.id).order_by(ConfigOverrideRow.id.desc()).limit(KEEP_VERSIONS)
        session.execute(delete(ConfigOverrideRow).where(ConfigOverrideRow.id.not_in(keep.scalar_subquery())))
        return row.id


def versions(db: Database) -> list[dict[str, Any]]:
    with db.session(write=False) as session:
        rows = session.scalars(select(ConfigOverrideRow).order_by(ConfigOverrideRow.id.desc())).all()
        return [
            {"id": r.id, "created_at": r.created_at, "note": r.note, "keys": len(flatten(r.data or {}))} for r in rows
        ]


def version(db: Database, version_id: int) -> dict[str, Any] | None:
    with db.session(write=False) as session:
        row = session.get(ConfigOverrideRow, version_id)
        return dict(row.data or {}) if row else None


def file_data() -> dict[str, Any]:
    data = yaml.safe_load(config_path().read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """``over`` deep-merged onto a copy of ``base`` (mappings merge, anything else replaces)."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """{"a": {"b": 1}} -> {"a.b": 1}: one entry per overridden value."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out |= flatten(value, path + ".")
        else:
            out[path] = value
    return out


def lookup(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def without(data: dict[str, Any], path: str) -> dict[str, Any]:
    """``data`` minus the value at a dotted path; mappings left empty go too."""
    out = copy.deepcopy(data)
    head, _, rest = path.partition(".")
    if head not in out:
        return out
    if rest and isinstance(out[head], dict):
        out[head] = without(out[head], rest)
        if not out[head]:
            del out[head]
    elif not rest:
        del out[head]
    return out


def in_force() -> dict[str, Any]:
    return active()
