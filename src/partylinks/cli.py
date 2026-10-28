"""Command line entry point.

    partylinks serve                 # API + scheduler
    partylinks scrape [SOURCE ...]   # run sources once (default: all enabled)
    partylinks sources               # list configured sources
    partylinks cleanup               # purge events that are over (retention)
    partylinks chillz-links          # page of Chillz links to open and save into the inbox
    partylinks copy-db --to URL      # copy the database to another one and verify it (dbcopy.py)
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from pydantic import ValidationError
from sqlalchemy import select

from partylinks.artistdb import ArtistBook
from partylinks.config import get_settings
from partylinks.db import Database
from partylinks.logs import setup_logging
from partylinks.scrapers import SCRAPERS, build_scraper, configured_sources
from partylinks.service import RunResult, ScrapeService

# Command results. INFO even under the WARNING default: they are what the command reports.
log = logging.getLogger("partylinks.cli")


def _scrape(names: list[str]) -> int:
    settings = get_settings()
    sources = configured_sources(settings)
    selected = names or [name for name, cfg in sources.items() if cfg.enabled]
    unknown = [n for n in selected if n not in sources]
    if unknown:
        log.error("unknown or unconfigured source(s): %s", ", ".join(unknown))
        return 2

    db = Database(settings.database.url, settings.database.echo, settings.database)
    db.create_schema()
    service = ScrapeService(settings, db)

    # All sources in parallel, each on its own thread / event loop / HTTP client.
    results = asyncio.run(service.run_sources(selected, trigger="cli"))
    service.shutdown()
    for name, r in zip(selected, results, strict=True):
        if not isinstance(r, RunResult):
            log.error("%-10s crashed  %s: %s", name, type(r).__name__, r)
            continue
        log.log(logging.ERROR if r.status == "failed" else logging.INFO,
                "%-10s %-8s found=%d saved=%d failed=%d%s", r.source, r.status, r.events_found, r.events_saved,
                r.events_failed, "  error=" + r.error if r.error else "")  # fmt: skip
    return 0 if all(isinstance(r, RunResult) and r.status != "failed" for r in results) else 1


def _serve(reload: bool = False) -> int:
    import uvicorn

    from partylinks.api import create_app
    from partylinks.config import project_path

    settings = get_settings()
    # Only these proxies may set the client address (X-Forwarded-For); the Server header is dropped.
    net = {"proxy_headers": True, "forwarded_allow_ips": ",".join(settings.security.trusted_proxies), "server_header": False,
           # Shed load past this many open connections (503) instead of slowing to a crawl; drop idle keep-alives fast.
           "limit_concurrency": 200, "timeout_keep_alive": 5}
    if reload:
        # Development: restart on any change under src/. Config changes don't need it (admin -> apply).
        # Only the worker writes the log file: two processes on one file break rotation on Windows.
        setup_logging(settings.logging.model_copy(update={"file": None}))
        uvicorn.run(
            "partylinks.api:create_app", factory=True, reload=True, reload_dirs=[str(project_path("src"))],
            host=settings.api.host, port=settings.api.port, log_config=None, **net,
        )  # fmt: skip
    else:
        uvicorn.run(create_app(settings), host=settings.api.host, port=settings.api.port, log_config=None, **net)
    return 0


def _chillz_links() -> int:
    """Write a clickable list of recent Chillz pages (from the public sitemap) for manual saving."""
    from partylinks.config import project_path
    from partylinks.db import EventRow
    from partylinks.scrapers.chillz import ChillzScraper, write_links_page

    settings = get_settings()
    scraper = build_scraper("chillz", settings)
    if not isinstance(scraper, ChillzScraper):
        raise TypeError("the 'chillz' source is not the Chillz scraper")

    async def discover() -> list:  # type: ignore[type-arg]
        async with scraper:
            return await scraper.candidate_pages()

    pages = asyncio.run(discover())
    db = Database(settings.database.url, options=settings.database)
    db.create_schema()
    with db.session() as session:
        imported = set(session.scalars(select(EventRow.source_event_id).where(EventRow.source == "chillz")))
    inbox = project_path(scraper.settings.inbox_dir or "data/chillz_inbox")
    target = project_path("data/chillz_links.html")
    write_links_page(target, pages, imported, inbox)
    log.info("%d pages (%d already imported) -> %s", len(pages), len(imported), target)
    return 0


def _cleanup() -> int:
    settings = get_settings()
    db = Database(settings.database.url, settings.database.echo, settings.database)
    db.create_schema()
    service = ScrapeService(settings, db)
    removed = asyncio.run(service.purge_expired())
    service.shutdown()
    log.info("removed %d expired events", removed)
    return 0


def _artists(action: str, names: list[str]) -> int:
    settings = get_settings()
    db = Database(settings.database.url, settings.database.echo, settings.database)
    db.create_schema()
    book = ArtistBook(db, settings.enrichment.lineup.artists_db)
    if action == "list":
        for name, seen, blocked in book.rows():
            log.info("%4d  %s%s", seen, "BLOCKED " if blocked else "", name)
        return 0
    for name in names:
        book.set(name, blocked=action == "block")
    log.info("%s: %d name(s); applied on the next scrape", action, len(names))
    return 0


def _copy_db(source: str | None, target: str, force: bool) -> int:
    from partylinks.dbcopy import CopyRefused, copy_database

    settings = get_settings()
    try:
        reports = copy_database(source or settings.database.url, target, settings.database, force=force)
    except CopyRefused as exc:
        log.error("copy-db refused: %s", exc)
        return 2
    bad = [name for name, r in reports.items() if not r.ok]
    log.log(logging.ERROR if bad else logging.INFO, "copy-db: %d tables, %d rows, %s", len(reports),
            sum(r.rows for r in reports.values()), f"MISMATCH in {', '.join(bad)}" if bad else "verified")  # fmt: skip
    return 1 if bad else 0


def _sources() -> int:
    for name, cfg in configured_sources(get_settings()).items():
        log.info("%-10s %s  cron='%s'", name, "enabled " if cfg.enabled else "disabled", cfg.cron)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="partylinks")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the REST API with the scheduler")
    serve.add_argument("--reload", action="store_true", help="restart automatically when code under src/ changes")
    scrape = sub.add_parser("scrape", help="run sources once")
    scrape.add_argument("sources", nargs="*", metavar="SOURCE", help=f"any of: {', '.join(SCRAPERS)}")
    sub.add_parser("sources", help="list configured sources")
    sub.add_parser("cleanup", help="purge events that are over (retention)")
    sub.add_parser("chillz-links", help="write a page of Chillz links to open and save into the inbox")
    artists = sub.add_parser("artists", help="curate the artist dictionary (used to split glued names)")
    artists.add_argument("action", choices=["list", "add", "block", "unblock"])
    artists.add_argument("names", nargs="*", metavar="NAME")
    copy = sub.add_parser("copy-db", help="copy every table to another database and verify it")
    copy.add_argument("--from", dest="source", metavar="URL", help="source database (default: the configured one)")
    copy.add_argument("--to", dest="target", metavar="URL", required=True, help="target database URL")
    copy.add_argument("--force", action="store_true", help="drop the target's tables first")
    args = parser.parse_args(argv)
    try:
        setup_logging(get_settings().logging, args.verbose)
        log.setLevel(logging.INFO)
        return _dispatch(args)
    except ValidationError as exc:  # broken config.yaml / PARTYLINKS_* env: say what, no traceback
        # Logging is not set up yet (the config is what failed): Python's fallback handler writes to stderr.
        log.error("invalid configuration:\n%s", exc)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "serve":
        return _serve(args.reload)
    if args.command == "scrape":
        return _scrape(args.sources)
    if args.command == "cleanup":
        return _cleanup()
    if args.command == "chillz-links":
        return _chillz_links()
    if args.command == "copy-db":
        return _copy_db(args.source, args.target, args.force)
    if args.command == "artists":
        return _artists(args.action, args.names)
    return _sources()


if __name__ == "__main__":
    raise SystemExit(main())
