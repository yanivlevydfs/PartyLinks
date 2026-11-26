"""The home page's genre / filter rows (/events/rails): ``home_rails:`` in settings.yaml, edited in the admin
config tab (config_overrides)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)
_logged: set[str] = set()


def log_once(key: str) -> None:
    """A row that fails to build is skipped; its error is logged once per process, not on every cache refresh."""
    if key not in _logged:
        _logged.add(key)
        log.exception("home row %r skipped: its settings (home_rails) do not build", key)


class RowCache:
    """Each home row's cards, kept apart from the whole /events/rails response: that one is rebuilt after every
    write (30-60 s), but a genre row barely moves in minutes, and 81 rows built at once took 42 s of queries
    (each matches its genres by text, a scan of the events; prod, 26.11.2026). A build refreshes at most
    ``per_build`` stale rows (the oldest first), the others serve their last cards until a later build.

    A row is stale after ``ttl`` seconds, and must be rebuilt at once when its settings change or when it
    lists one day and the day changed (``tag``). Seeded from the response snapshot after a deploy (``seed``),
    so a cold start refreshes in turns instead of building every row together.
    ponytail: per-process dict, like ResponseCache; a single uvicorn worker serves visitors.
    """

    def __init__(self) -> None:
        self._rows: dict[str, tuple[float, str, Any]] = {}  # key -> (built at, tag, cards)
        self._lock = threading.Lock()

    def seed(self, rails: dict[str, Any], tags: dict[str, str]) -> None:
        """Rows from an older response, as stale ones (built at 0); rows already cached are kept."""
        with self._lock:
            for key, cards in rails.items():
                if key in tags and key not in self._rows:
                    self._rows[key] = (0.0, tags[key], cards)

    def empty(self) -> bool:
        return not self._rows

    def rows(self, tags: dict[str, str], build: Callable[[str], Any], ttl: float, per_build: int) -> dict[str, Any]:
        """{key: cards} for every key of ``tags`` (in its order) that builds; ``build(key)`` raises to skip one."""
        now = time.monotonic()
        with self._lock:
            have = dict(self._rows)
        due = [k for k in tags if k not in have or have[k][1] != tags[k]]  # new, changed or a new day: now
        stale = sorted((k for k in tags if k not in due and now - have[k][0] >= ttl), key=lambda k: have[k][0])
        out: dict[str, Any] = {}
        for key in [*due, *stale[: max(per_build - len(due), 0)]]:
            try:
                cards = build(key)
            except Exception:  # noqa: BLE001 - one bad row must not take the home page down
                log_once(key)
                continue
            out[key] = cards
            with self._lock:
                self._rows[key] = (time.monotonic(), tags[key], cards)
        with self._lock:
            for gone in set(self._rows) - set(tags):  # a row taken out of the settings
                del self._rows[gone]
        return {k: out[k] if k in out else have[k][2] for k in tags if k in out or (k in have and k not in due)}
