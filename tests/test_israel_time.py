"""Israel time, whatever zone the server runs in: run also under TZ=Asia/Jerusalem and TZ=UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import update

from partylinks import seo
from partylinks.ads.routes import SponsoredIn
from partylinks.config import Settings
from partylinks.db import Database, EventRow, UTCDateTime, upsert_events
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent

IL = ZoneInfo("Asia/Jerusalem")


def test_a_naive_value_binds_as_utc_not_the_servers_zone() -> None:
    # The old astimezone(UTC) read it in the machine's zone: 10:00 on an Israeli server.
    assert UTCDateTime().process_bind_param(datetime(2026, 10, 31, 12, 0), None) == datetime(2026, 10, 31, 12, 0)


def test_the_night_dst_ends_binds_both_half_past_ones() -> None:
    # 25.10.2026: 02:00 IDT falls back to 01:00 IST, so 01:30 happens twice, an hour apart.
    bind = UTCDateTime().process_bind_param
    assert bind(datetime(2026, 10, 25, 1, 30, tzinfo=IL), None) == datetime(2026, 10, 24, 22, 30)
    assert bind(datetime(2026, 10, 25, 1, 30, fold=1, tzinfo=IL), None) == datetime(2026, 10, 24, 23, 30)


def test_a_campaign_ends_at_israels_midnight() -> None:
    winter = SponsoredIn(event_id="e", side="right", until="2026-11-05T23:59:00").until
    summer = SponsoredIn(event_id="e", side="right", until="2026-10-20T23:59:00").until
    assert winter == datetime(2026, 11, 5, 21, 59, tzinfo=UTC)
    assert summer == datetime(2026, 10, 20, 20, 59, tzinfo=UTC)
    assert SponsoredIn(event_id="e", side="left", until="2026-11-05T21:59:00Z").until == winter


def test_sitemap_lastmod_is_israels_day() -> None:
    settings = Settings(scheduler={"enabled": False}, media={"download": False})
    db = Database(settings.database.url)
    db.create_schema()
    event = ScrapedEvent(
        source="goout", source_event_id="late", url="https://go-out.example/late", title="Late night",
        start_at=datetime.now(UTC) + timedelta(days=2),
    )  # fmt: skip
    with db.session() as session:
        upsert_events(session, [Enricher(settings.enrichment, settings.timezone).enrich(event)])  # type: ignore[list-item]
        # 23:30 UTC on 31.10 is already 01:30 on 1.11 in Israel.
        session.execute(update(EventRow).values(last_seen_at=datetime(2026, 10, 31, 23, 30, tzinfo=UTC)))
    with db.session() as session:
        urls = "".join(seo.sitemap_urls(session, settings))
    assert "<lastmod>2026-11-01</lastmod>" in urls
