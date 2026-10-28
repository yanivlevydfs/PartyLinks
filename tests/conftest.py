from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# PARTYLINKS_TEST_PG_URL=postgresql://user:pass@host/db pytest  runs every test's database on PostgreSQL:
# each sqlite:/// URL a test opens is swapped for that database, emptied at the test's first open
# (sqlite+pysqlite:/// stays SQLite, for tests that need both).
PG_URL = os.environ.get("PARTYLINKS_TEST_PG_URL")


@pytest.fixture(autouse=True)
def _on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    if not PG_URL:
        return
    from partylinks.db import Base, Database

    original = Database.__init__
    emptied: list[bool] = []

    def init(self: Database, url: str, *args: Any, **kwargs: Any) -> None:
        original(self, PG_URL if url.startswith("sqlite:///") else url, *args, **kwargs)
        if not emptied:
            Base.metadata.drop_all(self.engine)
            emptied.append(True)

    monkeypatch.setattr(Database, "__init__", init)


@pytest.fixture(autouse=True)
def _private_artists_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing into the real artist dictionary (data/artists.db)."""
    monkeypatch.setenv("PARTYLINKS_ENRICHMENT__LINEUP__ARTISTS_DB", str(tmp_path / "artists.db"))


@pytest.fixture(autouse=True)
def _no_live_israeli_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """`israel_proxy` sources would fetch the live proxy feed and dial real proxies."""
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY_FEED", "")
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY", "")
