"""Scrape the `home: true` sources from this PC and upload each run to the live server.

Those sites refuse every server and proxy IP (Railway, AWS, the proxy pool) but answer an Israeli
home connection, so this runs here and the server saves the upload like one of its own runs
(enrichment, dedup, geocoding, run history).

    railway run --service web -- python scripts/home_runner.py              # every home source
    railway run --service web -- python scripts/home_runner.py tiuli haifamuni

``railway run`` supplies the admin token (PARTYLINKS_ADMIN__TOKEN) and the domain; its proxy
variables are dropped so requests leave from this PC's own IP. Windows Task Scheduler runs it
twice a day: see scripts/home_runner.bat.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Before the settings load: the server's proxies are what these sites refuse.
for name in ("PARTYLINKS_HTTP__SOURCE_PROXY", "PARTYLINKS_HTTP__ISRAEL_PROXY", "PARTYLINKS_HTTP__PROXY"):
    os.environ.pop(name, None)

import httpx

from partylinks.config import get_settings
from partylinks.scrapers import build_scraper, configured_sources, source_settings
from partylinks.scrapers.base import SourceBlockedError


async def scrape(source: str) -> dict[str, object]:
    settings = get_settings()
    timeout = source_settings(source, settings).run_timeout_seconds or settings.scheduler.run_timeout_seconds
    events, status, error = [], "success", None
    scraper = build_scraper(source, settings)
    try:
        async with asyncio.timeout(timeout), scraper:
            async for event in scraper.scrape():
                events.append(event.model_dump(mode="json"))
    except SourceBlockedError as exc:
        status, error = "blocked", str(exc)
    except TimeoutError:
        status, error = "failed", f"timed out after {timeout}s (home runner)"
    except Exception as exc:  # noqa: BLE001 - reported to the server as the run's error
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
    return {"events": events, "failures": scraper.failures, "status": status, "error": error}


def upload(source: str, run: dict[str, object]) -> str:
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    base = os.environ.get("PARTYLINKS_URL") or (f"https://{domain}" if domain else "http://localhost:8000")
    token = os.environ.get("PARTYLINKS_ADMIN__TOKEN", "")
    response = httpx.post(
        f"{base}/admin/api/ingest/{source}", json=run, headers={"Authorization": f"Bearer {token}"}, timeout=300
    )
    return f"{response.status_code} {response.text[:200]}"


def main(args: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    home = [n for n, s in configured_sources(get_settings()).items() if s.enabled and s.home]
    for source in args or home:
        run = asyncio.run(scrape(source))
        print(f"{source:<18} {run['status']:<8} {len(run['events']):>5} events  {run['error'] or ''}"[:200], flush=True)  # type: ignore[arg-type]
        print(f"{'':<18} upload: {upload(source, run)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
