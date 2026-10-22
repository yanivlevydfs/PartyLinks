from __future__ import annotations

import logging
from pathlib import Path

import pytest

from partylinks import status
from partylinks.config import Settings
from partylinks.db import Database


def test_startup_block_and_status_line_hide_secrets(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg = Settings(
        database={"url": f"sqlite:///{tmp_path / 'test.db'}"},
        scheduler={"enabled": False},
        http={"source_proxy": "http://user:secret@proxy.example:8080"},
        admin={"token": "s3cret-token"},
    )
    db = Database(cfg.database.url)
    db.create_schema()
    with caplog.at_level(logging.INFO, logger="partylinks.status"):
        status.log_startup(cfg, db, None)
        status.log_status(cfg, db, None)
        status.log_shutdown(cfg)
    text = caplog.text
    assert "Pulse 0.1.0 starting" in text and "scheduler  OFF" in text
    assert "proxy      proxy.example:8080" in text and "token set" in text
    assert "secret" not in text.replace("token set", "")
    assert "status: up 0h 0m | events 0 upcoming / 0 | runs 24h: none | scheduler off | clicks 24h: 0" in text
    assert "Pulse stopping after" in text
    db.engine.dispose()
