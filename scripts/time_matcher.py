"""Time one alert-matcher pass at production size (the memory rule: measure whole-table passes before pushing).

    python scripts/time_matcher.py [--events 28000] [--alerts 50000] [--new 500]

Runs on its own throw-away local PostgreSQL database (pgserver, data/time_matcher), never production.
Events are synthetic (the enricher's genres from their titles); alerts draw their genres, regions, slots,
free-only and when from the configured values, plus some artist / word searches.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from partylinks.accounts.models import UserRow
from partylinks.alerts import matcher
from partylinks.alerts.criteria import Criteria
from partylinks.alerts.models import AlertRow, AlertStateRow
from partylinks.config import DatabaseSettings, Settings
from partylinks.db import Base, Database, upsert_events
from partylinks.enrichment import Enricher
from partylinks.localpg import local_url
from partylinks.models import ScrapedEvent, TicketTier

WORDS = ["techno", "house", "trance", "hip hop", "jazz", "rock", "stand up", "party", "festival", "reggae",
         "afro", "disco", "karaoke", "theatre", "classical", "pop", "mizrahi", "greek", "drum and bass"]  # fmt: skip
CITIES = ["Tel Aviv-Yafo", "Haifa", "Jerusalem", "Be'er Sheva", "Eilat", "Herzliya", "Ramat Gan", "Netanya"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=28_000)
    ap.add_argument("--alerts", type=int, default=50_000)
    ap.add_argument("--new", type=int, default=500)
    args = ap.parse_args()
    rnd = random.Random(7)

    url = local_url(DatabaseSettings(local_dir="data/time_matcher", local_database="time_matcher"))
    settings = Settings(database={"url": url}, scheduler={"enabled": False})
    db = Database(url)
    Base.metadata.drop_all(db.engine)
    db.create_schema()

    t = time.perf_counter()
    enricher = Enricher(settings.enrichment, settings.timezone)
    now = datetime.now(UTC)
    batch: list[ScrapedEvent] = []
    for i in range(args.events):
        words = rnd.sample(WORDS, 2)
        batch.append(ScrapedEvent(
            source="synthetic", source_event_id=str(i), url=f"https://example.com/{i}",
            title=f"{words[0].title()} {words[1]} night {i}", start_at=now + timedelta(hours=rnd.randint(2, 24 * 60)),
            address=f"{rnd.choice(CITIES)}, Israel",
            tickets=[TicketTier(name="Entry", price=Decimal(rnd.choice([0, 0, 40, 80, 120])))],
        ))  # fmt: skip
        if len(batch) == 1000:
            with db.session() as s:
                upsert_events(s, [e for e in map(enricher.enrich, batch) if e])
            batch = []
    if batch:
        with db.session() as s:
            upsert_events(s, [e for e in map(enricher.enrich, batch) if e])
    print(f"events: {args.events} in {time.perf_counter() - t:.0f} s")

    e = settings.enrichment
    genres = sorted(set(e.genres) | set(e.genre_families))
    regions = ["tel_aviv", *(r.key for r in e.regions)]
    slots = [s.name for s in e.time_slots]
    users = [{"id": secrets.token_hex(16), "google_sub": f"s{i}", "email": f"u{i}@x.test", "name": f"U{i}"}
             for i in range(max(args.alerts // 5, 1))]  # fmt: skip
    alerts = []
    for i in range(args.alerts):
        c = Criteria(
            genres=rnd.sample(genres, rnd.randint(0, 3)) if rnd.random() < 0.8 else [],
            regions=rnd.sample(regions, rnd.randint(0, 2)),
            slots=rnd.sample(slots, rnd.randint(0, 1)) if slots else [],
            freeOnly=rnd.random() < 0.2,
            when=rnd.choice(["all", "all", "weekend", "week", "month"]),
            q=rnd.choice(WORDS) if rnd.random() < 0.15 else "",
        )
        if c.is_empty():
            c = Criteria(q=rnd.choice(WORDS))
        alerts.append({"user_id": users[i % len(users)]["id"], "name": "a", "criteria": c.model_dump(),
                       "criteria_hash": c.digest()})  # fmt: skip
    with db.session() as s:
        for start in range(0, len(users), 5000):
            s.execute(insert(UserRow).values(users[start : start + 5000]))
        for start in range(0, len(alerts), 5000):
            s.execute(insert(AlertRow).values(alerts[start : start + 5000]))
        # The last --new events are "new": first seen after the cursor.
        s.execute(text("UPDATE events SET first_seen_at = now() - interval '1 day'"))
        s.execute(text(f"UPDATE events SET first_seen_at = now() WHERE id IN (SELECT id FROM events ORDER BY random() LIMIT {args.new})"))
        s.add(AlertStateRow(id=1, cursor=datetime.now(UTC) - timedelta(hours=1)))
    distinct = len({a["criteria_hash"] for a in alerts})
    print(f"alerts: {args.alerts} ({distinct} distinct), users: {len(users)}, new events: {args.new}")

    t = time.perf_counter()
    hits = matcher.run(settings, db)
    print(f"matcher pass: {time.perf_counter() - t:.1f} s, {hits} hits")


if __name__ == "__main__":
    main()
