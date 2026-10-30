"""Starts the worker process beside the web server and keeps it running: restarted (with backoff) if
it ever exits or hangs (no heartbeat for ``worker_hung_after_seconds``), stopped with the server."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from partylinks.config import SchedulerSettings
from partylinks.db import utcnow

log = logging.getLogger(__name__)


class WorkerProcess:
    def __init__(self, settings: SchedulerSettings, state: Callable[[], dict[str, Any] | None] | None = None) -> None:
        """``state``: the worker's published state (``WorkerLink.state``); without it a hung worker is not detected."""
        self.settings, self.state = settings, state
        self._stopping = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._thread = threading.Thread(target=self._keep_running, name="worker-supervisor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _keep_running(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                # Same interpreter, environment and working directory; logs go to the same stdout / stderr.
                launched = utcnow()
                self._process = subprocess.Popen([sys.executable, "-m", "partylinks", "worker"])
                log.info("worker: process %d started", self._process.pid)
                code: int | str = self._watch(self._process, launched)
            except Exception as exc:  # e.g. out of memory or pids: this thread is the only restarter
                log.exception("worker: could not start the process")
                code = f"none ({type(exc).__name__})"
            if self._stopping.is_set():
                return
            ran = time.monotonic() - started
            stable = ran > self.settings.worker_stable_after_seconds
            backoff = 1.0 if stable else min(backoff * 2, self.settings.restart_backoff_max_seconds)
            log.error("worker: process exited with code %s after %.0fs; restarting in %.0fs", code, ran, backoff)
            self._stopping.wait(backoff)

    def _watch(self, process: subprocess.Popen[bytes], launched: datetime) -> int:
        """Wait for the process to exit; stop it if its heartbeat goes stale (the loop then restarts it)."""
        while True:
            try:
                return process.wait(self.settings.heartbeat_seconds)
            except subprocess.TimeoutExpired:
                pass
            if not self._stopping.is_set() and self._hung(process.pid, launched):
                log.error("worker: process %d hung (no heartbeat for %.0fs), stopping it", process.pid,
                          self.settings.worker_hung_after_seconds)  # fmt: skip
                self._end(process)

    def _hung(self, pid: int, launched: datetime) -> bool:
        """Only a heartbeat of this process counts: until its first one (starting up) it is never judged."""
        if self.state is None:
            return False
        try:
            state = self.state()
        except Exception as exc:  # noqa: BLE001  a database hiccup never kills the worker
            log.warning("worker: state not read: %s", exc)
            return False
        if not state or state["pid"] != pid or state["started_at"] < launched:
            return False
        return state["heartbeat_age_seconds"] > self.settings.worker_hung_after_seconds

    def stop(self) -> None:
        """Ask the worker to stop (SIGTERM: running sources are recorded as interrupted), then kill it
        if it has not exited within ``worker_stop_seconds``."""
        self._stopping.set()
        process = self._process
        if process is not None:
            self._end(process)

    def _end(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(self.settings.worker_stop_seconds)
        except subprocess.TimeoutExpired:
            log.warning("worker: still running after %ss, killing it", self.settings.worker_stop_seconds)
            process.kill()
