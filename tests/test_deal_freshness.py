"""Existing deals stay current: every change is recorded, free entry expires on time,
closed sale windows show as closed between scans, sources scan twice a day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, DealChangeRow, upsert_events
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.scrapers import configured_sources

NOW = datetime.now(UTC)


def make(tmp_path: Path) -> tuple[Settings, Database, Enricher]:
    settings = Settings(database={"url": f"sqlite:///{tmp_path / 'f.db'}"}, scheduler={"enabled": False},
                        media={"download": False})  # fmt: skip
    db = Database(settings.database.url)
    db.create_schema()
    return settings, db, Enricher(settings.enrichment, settings.timezone)


def scan(db: Database, enricher: Enricher, *tiers: TicketTier, sold_out: bool = False) -> None:
    event = ScrapedEvent(
        source="goout", source_event_id="moon", url="https://go-out.example/moon", title="THURSDAY MOON",
        start_at=NOW + timedelta(days=1), tickets=list(tiers), is_sold_out=sold_out,
    )  # fmt: skip
    priced = [t.price for t in tiers if t.price is not None and t.is_active]
    event.min_price, event.max_price = (min(priced), max(priced)) if priced else (None, None)
    with db.session() as session:
        upsert_events(session, [enricher.enrich(event)])  # type: ignore[list-item]


def kinds(db: Database) -> list[str]:
    with db.session() as session:
        return [c.kind for c in session.query(DealChangeRow).order_by(DealChangeRow.id)]


# ------------------------------------------------------------------ 1. change history + feed
def test_every_deal_change_between_scans_is_recorded(tmp_path: Path) -> None:
    settings, db, enricher = make(tmp_path)
    free = TicketTier(name="Free before 00:30", price=Decimal(0))
    entry = TicketTier(name="Entry", price=Decimal(80))

    scan(db, enricher, free, entry)  # first sight
    scan(db, enricher, free, entry)  # nothing changed -> no record
    scan(db, enricher, TicketTier(name="Entry", price=Decimal(60)))  # free gone, price dropped
    scan(db, enricher, TicketTier(name="Entry", price=Decimal(60), status=TierStatus.SOLD_OUT), sold_out=True)

    # Losing the free tier also raises the lowest price 0 -> 60: both are real changes for a buyer.
    assert kinds(db) == ["new", "price_up", "free_entry_ended", "tiers_changed", "sold_out", "tiers_changed"]
    with db.session() as session:
        tiers = [c.detail for c in session.query(DealChangeRow).where(DealChangeRow.kind == "tiers_changed")]
    assert tiers[0] == "-Free before 00:30; ~Entry: 80 -> 60"
    assert tiers[1] == "~Entry: on_sale -> sold_out"

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        feed = client.get("/changes", params={"kind": "free_entry_ended"}).json()
        assert [(c["kind"], c["title"]) for c in feed] == [("free_entry_ended", "THURSDAY MOON")]
        detail = client.get("/events/goout:moon").json()
        assert detail["changes"][0]["kind"] == "tiers_changed"  # newest first


def test_price_drop_is_its_own_change(tmp_path: Path) -> None:
    _, db, enricher = make(tmp_path)
    scan(db, enricher, TicketTier(name="Entry", price=Decimal(100)))
    scan(db, enricher, TicketTier(name="Entry", price=Decimal(70)))
    with db.session() as session:
        drop = session.query(DealChangeRow).where(DealChangeRow.kind == "price_down").one()
    assert (drop.old, drop.new) == ("100", "70")


# ------------------------------------------------------------------ 2. free entry expires on time
def test_free_entry_stops_counting_when_its_window_ends(tmp_path: Path) -> None:
    settings, db, enricher = make(tmp_path)
    ended = TicketTier(name="Free until 23:30", price=Decimal(0), sale_end=NOW - timedelta(minutes=1))
    scan(db, enricher, ended, TicketTier(name="Entry", price=Decimal(80)))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert client.get("/events", params={"free_entry": True}).json()["total"] == 0  # 23:31: no longer free
        assert client.get("/events", params={"free_entry": False}).json()["total"] == 1


def test_free_entry_still_open_is_found(tmp_path: Path) -> None:
    settings, db, enricher = make(tmp_path)
    scan(db, enricher, TicketTier(name="Free until 23:30", price=Decimal(0), sale_end=NOW + timedelta(hours=2)))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        events = client.get("/events", params={"free_entry": True}).json()
        assert events["total"] == 1 and events["items"][0]["group_free_entry_until"] is not None


# ------------------------------------------------------------------ 3. live tier status
def test_tier_whose_sale_window_closed_shows_closed_between_scans(tmp_path: Path) -> None:
    settings, db, enricher = make(tmp_path)
    scan(
        db, enricher,
        TicketTier(name="Early bird", price=Decimal(50), sale_end=NOW - timedelta(minutes=5)),
        TicketTier(name="Last chance", price=Decimal(90), sale_start=NOW + timedelta(hours=1)),
        TicketTier(name="Entry", price=Decimal(70)),
    )  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        tiers = {t["name"]: (t["status"], t["is_active"]) for t in client.get("/events/goout:moon").json()["tickets"]}
    assert tiers == {
        "Early bird": ("closed", False),
        "Last chance": ("coming_soon", False),
        "Entry": ("on_sale", True),
    }


# ------------------------------------------------------------------ 4. twice-daily, staggered scans
def test_every_source_scans_twice_a_day_at_its_own_time() -> None:
    settings = Settings()
    start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    firsts = {}
    for name, source in configured_sources(settings).items():
        trigger = CronTrigger.from_crontab(source.cron, timezone="UTC")
        first = trigger.get_next_fire_time(None, start)
        second = trigger.get_next_fire_time(first, first + timedelta(seconds=1))
        assert second - first == timedelta(hours=12), f"{name} runs every {second - first}"
        firsts[name] = first
    assert len(set(firsts.values())) == len(firsts), "two sources share a start time"
