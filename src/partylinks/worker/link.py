"""The web process's side of the worker: its published state, and commands sent to it."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select

from partylinks.config import SchedulerSettings
from partylinks.db import Database, utcnow
from partylinks.worker.models import STATE_ID, WorkerCommandRow, WorkerStateRow

log = logging.getLogger(__name__)


class WorkerTimeout(RuntimeError):
    """The worker did not answer a command in time (busy, restarting or down)."""


class WorkerLink:
    def __init__(self, db: Database, settings: SchedulerSettings) -> None:
        self.db, self.settings = db, settings

    def state(self) -> dict[str, Any] | None:
        """The worker's last published state, with ``alive`` (a heartbeat within ``worker_stale_seconds``)."""
        with self.db.engine.connect() as conn:
            row = conn.execute(select(WorkerStateRow).where(WorkerStateRow.id == STATE_ID)).first()
        if row is None:
            return None
        state = dict(row._mapping)
        age = (utcnow() - state["heartbeat_at"]).total_seconds()
        state["heartbeat_age_seconds"] = round(age, 1)
        state["alive"] = age < self.settings.worker_stale_seconds and state["scheduler"] != "stopped"
        return state

    def next_runs(self) -> dict[str, datetime | None]:
        """Next run of every scheduled job (``scheduler.next_run_times`` of the worker's scheduler)."""
        state = self.state() or {}
        return {j["id"]: datetime.fromisoformat(j["next_run_at"]) if j["next_run_at"] else None for j in state.get("jobs") or []}

    def send(self, command: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue ``command`` and wait for the worker's answer (it reads commands every heartbeat)."""
        with self.db.engine.begin() as conn:
            command_id = conn.scalar(insert(WorkerCommandRow).values(command=command, args=args).returning(WorkerCommandRow.id))
        deadline = time.monotonic() + self.settings.command_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(0.25)
            with self.db.engine.connect() as conn:
                row = conn.execute(select(WorkerCommandRow.done_at, WorkerCommandRow.result).where(WorkerCommandRow.id == command_id)).first()
            if row and row.done_at:
                return row.result or {}
        raise WorkerTimeout(f"the worker did not answer {command!r} within {self.settings.command_timeout_seconds:.0f}s")

    async def follow_writes(self) -> None:
        """Until cancelled: when the worker commits data, this process's cached pages go stale too
        (``Database.changes`` is per process)."""
        seen: int | None = None
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            try:
                state = await asyncio.to_thread(self.state)
            except Exception as exc:  # noqa: BLE001  the next beat tries again
                log.warning("worker link: state not read: %s", exc)
                continue
            changes = state["changes"] if state else None
            if seen is not None and changes != seen:
                Database.changes += 1
            seen = changes
