"""Dry-run one source and check it against the new-source checklist (docs/adding-a-source.md).

    python scripts/check_source.py <name>            # scrape live, enrich, print the report
    python scripts/check_source.py <name> --show 10  # also print the first 10 events

Read-only: nothing is written to any database and the scheduler is not involved. The scraper runs
as configured in config/settings.yaml (locally without the Railway proxy), each event goes through
Enricher.enrich, and the report counts what the checklist requires: a place on the map, a site
genre, venue, artwork, a ticket link, no past events, a cron minute no other source uses.
Exit code 1 when a hard rule fails (no events, a past event, an event without a genre, a cron clash).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.retention import is_current
from partylinks.scrapers import build_scraper


def _pct(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n // total if total else 0}%)"


async def check(name: str, show: int) -> int:
    settings = get_settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    pairs = []
    async with build_scraper(name, settings) as scraper:
        async for event in scraper.scrape():
            pairs.append((event, enricher.enrich(event)))
        failures = list(scraper.failures)
    kept = [e for _, e in pairs if e is not None]
    total = len(kept)
    past = [e for e in kept if not is_current(e.start_at, e.end_at, settings.retention, settings.timezone)]
    no_genre = [e for e in kept if not e.genres]
    crons = {n: str((b or {}).get("cron") or "") for n, b in settings.sources.items() if (b or {}).get("enabled", True)}

    def slots(cron: str) -> set[tuple[str, str]]:  # (minute, hour) pairs; "*" or ranges count as clashing
        minute, hour = ([*cron.split(), "*", "*"])[:2]
        return {(m, h) for m in minute.split(",") for h in hour.split(",")}

    mine = slots(crons.get(name, ""))
    clash = [
        o
        for o, c in crons.items()
        if o != name and any(m == m2 and (h == h2 or "*" in (h, h2)) for m, h in mine for m2, h2 in slots(c))
    ]

    print(f"source {name}: scraped {len(pairs)}, kept after enrichment {total}, failures {len(failures)}")
    print(f"  on the map (lat/lng)  {_pct(sum(1 for e in kept if e.latitude and e.longitude), total)}")
    print(f"  site genre            {_pct(total - len(no_genre), total)}")
    print(f"  venue name            {_pct(sum(1 for e in kept if e.venue_name), total)}")
    print(f"  city                  {_pct(sum(1 for e in kept if e.city), total)}")
    print(f"  artwork               {_pct(sum(1 for e in kept if e.images or e.image_url), total)}")
    print(f"  ticket link           {_pct(sum(1 for e in kept if e.extra.get('purchase_url') or e.tickets), total)}")
    print(f"  price                 {_pct(sum(1 for e in kept if e.min_price is not None), total)}")
    print(f"  time known            {_pct(sum(1 for e in kept if e.time_known), total)}")
    print(f"  cities   {Counter(e.city for e in kept).most_common(8)}")
    print(f"  genres   {Counter(g for e in kept for g in e.genres).most_common(25)}")
    print(f"  declared {Counter(g for e in kept for g in e.source_genres).most_common(25)}")
    for what in failures[:5]:
        print(f"  failure  {what}")
    for e in no_genre[:15]:
        print(f"  NO GENRE {e.title!r} declared={e.source_genres}")
    for e in past[:5]:
        print(f"  PAST     {e.title!r} {e.start_at} - {e.end_at}")
    if clash:
        print(f"  CRON CLASH {crons.get(name)} shares a minute and hour with {clash}")
    for e in kept[:show]:
        print(
            f"  - {e.title} | {e.start_at} | {e.venue_name} | {e.city} | {e.latitude},{e.longitude} | {e.genres} | {e.url}"
        )

    hard = [m for m, bad in (("no events", not total), ("past events", past), ("events without a genre", no_genre),
                             ("cron clash", clash)) if bad]  # fmt: skip
    print("FAIL: " + ", ".join(hard) if hard else "OK: every hard rule holds; read the coverage lines above")
    return 1 if hard else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--show", type=int, default=3)
    args = parser.parse_args()
    sys.exit(asyncio.run(check(args.name, args.show)))


if __name__ == "__main__":
    main()
