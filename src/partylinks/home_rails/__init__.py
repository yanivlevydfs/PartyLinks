"""The home page's genre / filter rows (/events/rails): ``home_rails:`` in settings.yaml, edited in the admin
config tab (config_overrides)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_logged: set[str] = set()


def log_once(key: str) -> None:
    """A row that fails to build is skipped; its error is logged once per process, not on every cache refresh."""
    if key not in _logged:
        _logged.add(key)
        log.exception("home row %r skipped: its settings (home_rails) do not build", key)
