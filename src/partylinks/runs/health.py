"""After every run: is the source healthy? A bad run (failed, blocked, no events, or far fewer than
usual) is run again after ``scheduler.retry_after_minutes`` (15 min, 1 h, 3 h); still bad after the
last retry, the source is paused (``paused:<source>`` in the flags table, so it survives restarts)
and the admins get an email. Any healthy run (an admin's "run now" after the fix) unpauses it.

ponytail: strike counts and retry timers live in memory; a restart mid-chain starts the count again.
"""

from __future__ import annotations

import html
import logging
import statistics
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from partylinks.config import Settings
from partylinks.db import Database, FlagRow, ScrapeRunRow, flag_on

if TYPE_CHECKING:
    from partylinks.service import RunResult

log = logging.getLogger(__name__)
RETRY_TRIGGER = "retry"
COUNTED = ("schedule", RETRY_TRIGGER)  # an admin's own run unpauses when healthy, never strikes
USUAL_RUNS = 5  # "usual" = median events found by this many recent good runs


def pause_key(source: str) -> str:
    return f"paused:{source}"


def set_paused(db: Database, source: str, on: bool) -> None:
    """Pause / unpause a source's scheduled runs (kept across restarts)."""
    with db.session() as session:
        row = session.get(FlagRow, pause_key(source))
        if row is None:
            session.add(FlagRow(key=pause_key(source), on=on))
        else:
            row.on = on


def paused_sources(session: Any) -> list[str]:
    keys = session.scalars(select(FlagRow.key).where(FlagRow.key.like("paused:%"), FlagRow.on.is_(True)))
    return sorted(k.removeprefix("paused:") for k in keys)


class RunHealth:
    def __init__(
        self, settings: Settings, db: Database, start: Callable[[str, str], Any], stopping: threading.Event
    ) -> None:
        self.settings, self.db, self._start, self._stopping = settings, db, start, stopping
        self._strikes: dict[str, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ paused flag
    def paused(self, source: str) -> bool:
        with self.db.session(write=False) as session:
            return flag_on(session, pause_key(source))

    def set_paused(self, source: str, on: bool) -> None:
        set_paused(self.db, source, on)
        if not on:
            with self._lock:
                self._strikes.pop(source, None)

    # ------------------------------------------------------------------ verdict
    def problem(self, result: RunResult) -> str | None:
        """Why this run is bad, or None when it is healthy."""
        cfg = self.settings.scheduler
        if result.status in ("failed", "blocked"):
            return f"{result.status}: {result.error or 'no error text'}"
        if result.events_found == 0:
            return "no events found"
        with self.db.session(write=False) as session:
            found = session.scalars(
                select(ScrapeRunRow.events_found)
                .where(ScrapeRunRow.source == result.source, ScrapeRunRow.id != result.run_id,
                       ScrapeRunRow.status.in_(("success", "partial")), ScrapeRunRow.events_found > 0)
                .order_by(ScrapeRunRow.id.desc()).limit(USUAL_RUNS)
            ).all()  # fmt: skip
        usual = statistics.median(found) if found else 0
        if usual >= cfg.low_count_min_usual and result.events_found < cfg.low_count_ratio * usual:
            return f"only {result.events_found} events, usually {usual:g}"
        return None

    # ------------------------------------------------------------------ after a run
    def after_run(self, result: RunResult, trigger: str) -> None:
        if result.status == "interrupted" or self._stopping.is_set():
            return
        try:
            reason = self.problem(result)
        except Exception:
            log.exception("health check of %s failed", result.source)
            return
        source = result.source
        if reason is None:
            with self._lock:
                self._strikes.pop(source, None)
            if self.paused(source):
                self.set_paused(source, False)
                log.warning("source %s healthy again: unpaused", source)
            return
        if trigger not in COUNTED:
            return
        delays = self.settings.scheduler.retry_after_minutes
        with self._lock:
            strikes = self._strikes[source] = self._strikes.get(source, 0) + 1
        if strikes <= len(delays):
            minutes = delays[strikes - 1]
            log.warning("source %s bad run (%s): retry %d in %g min", source, reason, strikes, minutes)
            timer = threading.Timer(minutes * 60, self._retry, (source,))
            timer.daemon = True
            timer.start()
            return
        self.set_paused(source, True)
        log.error("source %s paused after %d bad runs: %s", source, strikes, reason)
        self._email(source, strikes, reason, result.run_id)

    def _retry(self, source: str) -> None:
        try:  # on a timer thread: an error here would vanish from the log
            if self._stopping.is_set() or self.paused(source):
                return
            self._start(source, RETRY_TRIGGER)
        except Exception as exc:  # noqa: BLE001 - busy (already running: that run is the retry) or stopping
            log.info("retry of %s not started: %s", source, exc)

    def _email(self, source: str, strikes: int, reason: str, run_id: int) -> None:
        if not self.settings.scheduler.email_on_pause:
            return
        from partylinks.mail import send as mailer  # local: mail pulls in the accounts package

        admins = [a.strip() for a in self.settings.accounts.admin_emails if a.strip()]
        if not admins or not mailer.ready(self.settings):
            log.warning("source %s paused: no admin email sent (no admin_emails or mail not set up)", source)
            return
        admin = self.settings.site.base_url.rstrip("/") + "/admin"
        subject = f"SPOTX: source {source} paused after {strikes} bad runs"
        text = (f"{source} was paused: {reason}\n\nTried {strikes} times (retries after "
                f"{', '.join(f'{m:g}' for m in self.settings.scheduler.retry_after_minutes)} min). "
                f"Fix it, then press \"run now\" in the admin: a healthy run unpauses it.\n{admin}")  # fmt: skip
        body = "".join(f"<p>{html.escape(line)}</p>" for line in text.split("\n") if line)
        messages = [mailer.Message(user_id="admin", to=to, subject=subject, html=body, text=text,
                                   unsubscribe_url=admin, tags={"kind": "source_paused"}) for to in admins]  # fmt: skip
        try:
            mailer.send(self.settings, messages, key=f"source-paused:{source}:{run_id}")
        except Exception:
            log.exception("source %s paused: admin email failed", source)
