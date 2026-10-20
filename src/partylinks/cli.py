"""Command line entry point.

    partylinks serve                 # API + scheduler
    partylinks scrape [SOURCE ...]   # run sources once (default: all enabled)
    partylinks sources               # list configured sources
    partylinks cleanup               # purge events that are over (retention)
    partylinks chillz-links          # page of Chillz links to open and save into the inbox
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError
from sqlalchemy import select

from partylinks.artists import ArtistBook
from partylinks.config import get_settings
from partylinks.db import Database
from partylinks.logs import setup_logging
from partylinks.scrapers import SCRAPERS, build_scraper, configured_sources
from partylinks.service import RunResult, ScrapeService


def _scrape(names: list[str]) -> int:
    settings = get_settings()
    sources = configured_sources(settings)
    selected = names or [name for name, cfg in sources.items() if cfg.enabled]
    unknown = [n for n in selected if n not in sources]
    if unknown:
        print(f"unknown or unconfigured source(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    db = Database(settings.database.url, settings.database.echo)
    db.create_schema()
    service = ScrapeService(settings, db)

    # All sources in parallel, each on its own thread / event loop / HTTP client.
    results = asyncio.run(service.run_sources(selected, trigger="cli"))
    service.shutdown()
    for name, r in zip(selected, results, strict=True):
        if not isinstance(r, RunResult):
            print(f"{name:<10} crashed  {type(r).__name__}: {r}")
            continue
        print(f"{r.source:<10} {r.status:<8} found={r.events_found} saved={r.events_saved} "
              f"failed={r.events_failed}{'  error=' + r.error if r.error else ''}")  # fmt: skip
    return 0 if all(isinstance(r, RunResult) and r.status != "failed" for r in results) else 1


def _serve(reload: bool = False) -> int:
    import uvicorn

    from partylinks.api import create_app
    from partylinks.config import project_path

    settings = get_settings()
    # Only these proxies may set the client address (X-Forwarded-For); the Server header is dropped.
    net = {"proxy_headers": True, "forwarded_allow_ips": ",".join(settings.security.trusted_proxies), "server_header": False}
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
    db = Database(settings.database.url)
    db.create_schema()
    with db.session() as session:
        imported = set(session.scalars(select(EventRow.source_event_id).where(EventRow.source == "chillz")))
    inbox = project_path(scraper.settings.inbox_dir or "data/chillz_inbox")
    target = project_path("data/chillz_links.html")
    write_links_page(target, pages, imported, inbox)
    print(f"{len(pages)} pages ({len(imported)} already imported) -> {target}")
    return 0


def _cleanup() -> int:
    settings = get_settings()
    db = Database(settings.database.url, settings.database.echo)
    db.create_schema()
    service = ScrapeService(settings, db)
    removed = asyncio.run(service.purge_expired())
    service.shutdown()
    print(f"removed {removed} expired events")
    return 0


def _artists(action: str, names: list[str]) -> int:
    book = ArtistBook(get_settings().enrichment.lineup.artists_db)
    if action == "list":
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # cp1252 console vs "BĘÃTFÓØT"
        for name, seen, blocked in book.rows():
            print(f"{seen:>4}  {'BLOCKED ' if blocked else ''}{name}")
        return 0
    for name in names:
        book.set(name, blocked=action == "block")
    print(f"{action}: {len(names)} name(s); applied on the next scrape")
    return 0


def _sources() -> int:
    for name, cfg in configured_sources(get_settings()).items():
        print(f"{name:<10} {'enabled ' if cfg.enabled else 'disabled'}  cron='{cfg.cron}'")
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
    args = parser.parse_args(argv)
    try:
        setup_logging(get_settings().logging, args.verbose)
        return _dispatch(args)
    except ValidationError as exc:  # broken config.yaml / PARTYLINKS_* env: say what, no traceback
        print(f"invalid configuration:\n{exc}", file=sys.stderr)
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
    if args.command == "artists":
        return _artists(args.action, args.names)
    return _sources()


if __name__ == "__main__":
    raise SystemExit(main())
