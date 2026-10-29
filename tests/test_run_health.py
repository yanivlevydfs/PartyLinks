"""Run manager: a bad run is retried, a source still bad after the retries is paused, a healthy run unpauses it."""

from __future__ import annotations

import os
import threading

from partylinks.config import Settings
from partylinks.db import Database, ScrapeRunRow
from partylinks.runs import health
from partylinks.runs.health import RunHealth
from partylinks.service import RunResult


def _health(monkeypatch, retries: list) -> RunHealth:
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    settings = Settings()
    settings.scheduler.retry_after_minutes = [15, 60, 180]
    timers: list = []

    class Timer:  # records the delay instead of waiting
        def __init__(self, seconds, fn, args) -> None:
            timers.append(seconds)
            self.daemon = False

        def start(self) -> None:
            retries.append(timers[-1] / 60)

    monkeypatch.setattr(health.threading, "Timer", Timer)
    monkeypatch.setattr(RunHealth, "_email", lambda self, *a: retries.append("email"))
    return RunHealth(settings, db, lambda source, trigger: None, threading.Event())


def _run(db: Database, found: int, status: str = "success") -> RunResult:
    with db.session() as session:
        row = ScrapeRunRow(source="goout", trigger="schedule", status=status, events_found=found)
        session.add(row)
        session.flush()
        return RunResult(row.id, "goout", status, found, found, 0, None if status == "success" else "boom")


def test_bad_runs_retry_then_pause_and_a_healthy_run_unpauses(monkeypatch) -> None:
    retries: list = []
    h = _health(monkeypatch, retries)
    for _ in range(5):
        h.after_run(_run(h.db, 100), "schedule")  # usual: 100 events
    assert retries == [] and h.problem(_run(h.db, 50)) is None
    assert "usually 100" in (h.problem(_run(h.db, 20)) or "")  # under 30% of usual
    assert h.problem(_run(h.db, 0)) == "no events found"
    for _ in range(3):
        h.after_run(_run(h.db, 0, "failed"), "retry")
    assert retries == [15, 60, 180] and not h.paused("goout")
    h.after_run(_run(h.db, 0, "blocked"), "retry")
    assert retries[-1] == "email" and h.paused("goout")
    h.after_run(_run(h.db, 0, "failed"), "admin")  # an admin's failed run: no strike, no email
    assert retries.count("email") == 1 and h.paused("goout")
    h.after_run(_run(h.db, 90), "admin")  # fixed
    assert not h.paused("goout")


def test_runs_wait_for_a_free_slot(monkeypatch) -> None:
    from partylinks.service import ScrapeService

    settings = Settings()
    settings.scheduler.max_parallel_runs = 2
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    service = ScrapeService(settings, db)
    try:
        assert service._executor._max_workers == 2
    finally:
        service.shutdown()
