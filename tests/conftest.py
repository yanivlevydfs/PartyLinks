from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_artists_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing into the real artist dictionary (data/artists.db)."""
    monkeypatch.setenv("PARTYLINKS_ENRICHMENT__LINEUP__ARTISTS_DB", str(tmp_path / "artists.db"))
