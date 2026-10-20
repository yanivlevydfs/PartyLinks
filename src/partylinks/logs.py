"""One logging setup for every process (CLI, API, `serve --reload` workers, uvicorn).

* every line has the same shape, uvicorn's own and access lines included;
* DEBUG/INFO go to stdout and WARNING+ to stderr (hosts such as Railway show stderr as errors);
* ``format: json`` writes one JSON object per line (``level`` / ``message`` / ``logger`` / ``time``)
  for log platforms; ``text`` is for people;
* streams are UTF-8 with ``\\n`` endings whatever the console code page (Hebrew titles never
  crash a log call on Windows);
* an optional rotating UTF-8 file (``logging.file``) gets the same lines;
* quiet by default (``level: WARNING``, request lines only for 4xx/5xx); debug mode logs
  everything and can be switched on at runtime with :func:`set_debug`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from io import TextIOWrapper
from typing import IO, Any

from partylinks.config import LOG_FORMAT, LoggingSettings, project_path

# Libraries that log every request; only their problems are worth a line.
_QUIET = ("httpx", "httpcore", "watchfiles.main")
_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")
_MARK = "_partylinks_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class _Below(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.level


class _RotatingFile(logging.handlers.RotatingFileHandler):
    def _open(self) -> IO[str]:  # "\n" endings on Windows too (the stdlib opens in text mode)
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors, newline="\n")


class _FailedRequestsOnly(logging.Filter):
    """uvicorn access lines carry (client, method, path, http_version, status): keep 4xx/5xx."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        try:
            return len(args) < 5 or int(args[4]) >= 400
        except (TypeError, ValueError):
            return True


_failed_only = _FailedRequestsOnly()
_level = logging.WARNING  # the configured level, restored when debug is switched off


def debug_enabled() -> bool:
    return logging.getLogger().level == logging.DEBUG and _failed_only not in logging.getLogger("uvicorn.access").filters


def set_debug(on: bool) -> None:
    """Everything (DEBUG, every request, HTTP client lines) or back to the configured level."""
    logging.getLogger().setLevel(logging.DEBUG if on else _level)
    access = logging.getLogger("uvicorn.access")
    if on:
        access.removeFilter(_failed_only)
    else:
        access.addFilter(_failed_only)
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.DEBUG if on else logging.WARNING)


class _UvicornName(logging.Filter):
    """uvicorn logs its lifecycle as "uvicorn.error" even at INFO; show it as "uvicorn"."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return True


def _utf8(stream: IO[str]) -> IO[str]:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")
    return stream


def setup_logging(settings: LoggingSettings, verbose: bool = False) -> None:
    """Idempotent: replaces the handlers this function added before, keeps any others."""
    formatter: logging.Formatter = JsonFormatter() if settings.format == "json" else logging.Formatter(LOG_FORMAT)
    out = logging.StreamHandler(_utf8(sys.stdout))
    out.addFilter(_Below(logging.WARNING))
    err = logging.StreamHandler(_utf8(sys.stderr))
    err.setLevel(logging.WARNING)
    handlers: list[logging.Handler] = [out, err]
    if settings.file:
        path = project_path(settings.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(_RotatingFile(
            path, maxBytes=settings.file_max_mb * 1024 * 1024, backupCount=settings.file_backups, encoding="utf-8",
        ))  # fmt: skip

    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_UvicornName())
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    global _level
    _level = getattr(logging, settings.level)
    # uvicorn's loggers go through the root handlers instead of their own colour-coded ones.
    for name in _UVICORN:
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True
    logging.getLogger("uvicorn.access").disabled = not settings.access_log
    set_debug(verbose or settings.debug)
