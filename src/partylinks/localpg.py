"""The local PostgreSQL server for localhost and tests: an embedded one (pgserver), started on demand
in ``database.local_dir`` and stopped with the last process using it. Production never comes here:
there ``PARTYLINKS_DATABASE__URL`` names the server.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from partylinks.config import DatabaseSettings, project_path

log = logging.getLogger(__name__)
START_ATTEMPTS = 4
logging.getLogger("pgserver").setLevel(logging.WARNING)  # its start / stop chatter (stop runs at exit, after logging closed)


def local_url(options: DatabaseSettings) -> str:
    """URL of ``options.local_database`` on the local server, which is started (and the database
    created, collation "C") when needed."""
    return _start(str(project_path(options.local_dir)), options.local_database)


@lru_cache(maxsize=8)
def _start(directory: str, database: str) -> str:
    try:
        import pgserver
    except ImportError as exc:  # production image: no local server, the URL must be set
        raise RuntimeError(
            "no database URL: set PARTYLINKS_DATABASE__URL, or install the dev dependencies (pgserver) "
            "for a local PostgreSQL server"
        ) from exc
    uri = _server_uri(pgserver, directory)
    admin = create_engine(uri, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": database}):
            try:
                conn.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\' LC_COLLATE \'C\' LC_CTYPE \'C\''))
                log.info("local PostgreSQL: created database %s in %s", database, directory)
            except DBAPIError as exc:  # the web server and the worker starting together: one of them creates it
                if getattr(exc.orig, "sqlstate", None) not in ("42P04", "23505"):  # duplicate database / key
                    raise
    admin.dispose()
    return make_url(uri).set(database=database).render_as_string(hide_password=False)


def _server_uri(pgserver: Any, directory: str) -> str:
    """Start (or join) the server in ``directory``; tried again while it recovers from a killed run,
    which can outlast pgserver's own start timeout."""
    for attempt in range(1, START_ATTEMPTS):
        try:
            return pgserver.get_server(directory, cleanup_mode="stop").get_uri()
        except Exception as exc:  # noqa: BLE001  retried, and the last attempt raises
            log.warning("local PostgreSQL: not started (%s), attempt %d/%d", exc, attempt, START_ATTEMPTS)
            time.sleep(2 * attempt)
    return pgserver.get_server(directory, cleanup_mode="stop").get_uri()
