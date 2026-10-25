from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_artists_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing into the real artist dictionary (data/artists.db)."""
    monkeypatch.setenv("PARTYLINKS_ENRICHMENT__LINEUP__ARTISTS_DB", str(tmp_path / "artists.db"))


@pytest.fixture(autouse=True)
def _no_live_israeli_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """`israel_proxy` sources would fetch the live proxy feed and dial real proxies."""
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY_FEED", "")
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY", "")
