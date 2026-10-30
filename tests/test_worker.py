"""The worker process and its link to the web process (partylinks/worker)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from partylinks.config import SchedulerSettings
from partylinks.db import Database
from partylinks.worker import supervisor
from partylinks.worker.link import WorkerLink
from partylinks.worker.runner import Worker
from tests.test_storage_api import settings_for


def test_the_worker_publishes_its_state_and_obeys_admin_commands(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.scheduler.heartbeat_seconds = 0.1
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    link = WorkerLink(db, settings.scheduler)

    async def scenario() -> tuple[dict, dict, dict, int]:
        worker = Worker(settings, db)
        worker.scheduler.start()
        beat = asyncio.create_task(worker._beat())
        job = worker.scheduler.get_jobs()[0].id
        paused = await asyncio.to_thread(link.send, "pause_job", {"id": job})
        await asyncio.sleep(0.3)  # the next heartbeat publishes it
        state = await asyncio.to_thread(link.state)
        missing = await asyncio.to_thread(link.send, "resume_job", {"id": "no-such-job"})
        beat.cancel()
        worker.scheduler.shutdown(wait=False)
        worker.service.shutdown()
        return paused, state or {}, missing, len(worker.scheduler.get_jobs())

    paused, state, missing, jobs = asyncio.run(scenario())
    assert paused["next_run_at"] is None  # paused
    assert state["alive"] and state["scheduler"] == "running" and len(state["jobs"]) == jobs
    assert next(j for j in state["jobs"] if j["id"] == paused["id"])["paused"]
    assert "error" in missing


def test_the_web_process_sees_the_workers_writes(tmp_path: Path) -> None:
    """Cached pages go stale when the worker commits data, not on its heartbeat."""
    settings = settings_for(tmp_path)
    settings.scheduler.heartbeat_seconds = 0.05
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()

    async def scenario() -> tuple[int, int]:
        worker = Worker(settings, db)
        await asyncio.to_thread(worker._publish)
        follow = asyncio.create_task(WorkerLink(db, settings.scheduler).follow_writes())
        await asyncio.sleep(0.2)
        before = Database.changes
        await asyncio.to_thread(worker._publish)  # a heartbeat alone: no data change
        await asyncio.sleep(0.2)
        quiet = Database.changes - before
        Database.changes += 5  # the worker committed data (in production: another process's counter)
        await asyncio.to_thread(worker._publish)
        await asyncio.sleep(0.2)
        follow.cancel()
        worker.service.shutdown()
        return quiet, Database.changes - before

    quiet, moved = asyncio.run(scenario())
    assert quiet == 0 and moved > 5


def test_a_worker_that_exits_is_started_again(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    starts: list[list[str]] = []
    stopped = threading.Event()

    class FakeProcess:
        def __init__(self, args: list[str]) -> None:
            starts.append(args)
            self.pid = len(starts)

        def wait(self, timeout: float | None = None) -> int:
            if len(starts) < 3:
                return 1  # crashed
            stopped.wait(timeout)
            return 0

        def poll(self) -> int | None:
            return 0 if stopped.is_set() else None

        def terminate(self) -> None:
            stopped.set()

        kill = terminate

    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeProcess)
    process = supervisor.WorkerProcess(SchedulerSettings(restart_backoff_max_seconds=0.01))
    process.start()
    deadline = time.monotonic() + 5
    while len(starts) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    process.stop()
    assert len(starts) == 3 and starts[0][-1] == "worker" and stopped.is_set()


def test_a_hung_worker_is_stopped_and_started_again(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Stale heartbeat of this process: stopped and restarted. Starting up (no heartbeat of its own yet), a
    fresh heartbeat or an unreadable state never stop it."""
    processes: list[Any] = []
    read = [0]

    class FakeProcess:
        def __init__(self, args: list[str]) -> None:
            self.pid, self.ended = 100 + len(processes), threading.Event()
            processes.append(self)

        def wait(self, timeout: float | None = None) -> int:
            if not self.ended.wait(timeout):
                raise subprocess.TimeoutExpired("worker", timeout or 0)
            return -15

        def poll(self) -> int | None:
            return -15 if self.ended.is_set() else None

        def terminate(self) -> None:
            self.stopped_at_read = read[0]
            self.ended.set()

        kill = terminate

    reads = iter([
        None,  # starting: no state yet
        {"pid": 1, "started_at": datetime.now(UTC), "heartbeat_age_seconds": 999},  # another process's row
        RuntimeError("database down"),
        {"pid": 100, "started_at": datetime.now(UTC) + timedelta(seconds=1), "heartbeat_age_seconds": 30},  # busy, alive
    ])

    def state() -> dict[str, Any] | None:
        read[0] += 1
        value = next(reads, {"pid": 100, "started_at": datetime.now(UTC) + timedelta(seconds=1), "heartbeat_age_seconds": 301})
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeProcess)
    process = supervisor.WorkerProcess(SchedulerSettings(heartbeat_seconds=0.01, restart_backoff_max_seconds=0.01), state)
    process.start()
    deadline = time.monotonic() + 5
    while len(processes) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    process.stop()
    assert len(processes) == 2 and processes[0].stopped_at_read == 5  # only the stale heartbeat stopped it
