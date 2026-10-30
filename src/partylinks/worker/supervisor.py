"""Starts the worker process beside the web server and keeps it running: restarted (with backoff) if
it ever exits, stopped with the server."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

from partylinks.config import SchedulerSettings

log = logging.getLogger(__name__)


class WorkerProcess:
    def __init__(self, settings: SchedulerSettings) -> None:
        self.settings = settings
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
                self._process = subprocess.Popen([sys.executable, "-m", "partylinks", "worker"])
                log.info("worker: process %d started", self._process.pid)
                code: int | str = self._process.wait()
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

    def stop(self) -> None:
        """Ask the worker to stop (SIGTERM: running sources are recorded as interrupted), then kill it
        if it has not exited within ``worker_stop_seconds``."""
        self._stopping.set()
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(self.settings.worker_stop_seconds)
        except subprocess.TimeoutExpired:
            log.warning("worker: still running after %ss, killing it", self.settings.worker_stop_seconds)
            process.kill()
