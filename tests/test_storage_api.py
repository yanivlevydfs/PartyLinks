"""Persistence, retention and API against a temporary SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, EventRow, ImageRow, upsert_events
from partylinks.enrichment import Enricher
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.retention import purge_expired


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database={"url": f"sqlite:///{tmp_path / 'test.db'}"},
        scheduler={"enabled": False},
        media={"download": False, "storage": "database"},
    )


def event(event_id: str, start: datetime, **extra: object) -> ScrapedEvent:
    fields: dict[str, object] = {
        "source": "test",
        "source_event_id": event_id,
        "url": f"https://example.com/{event_id}",
        "title": f"Techno night {event_id}",
        "start_at": start,
        "end_at": start + timedelta(hours=6),
        "address": "Tel Aviv-Yafo, Israel",
        "images": [f"https://example.com/{event_id}.jpg"],
        "tickets": [TicketTier(name="Free before 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(80))],
    }
    return ScrapedEvent(**(fields | extra))


def seed(settings: Settings, events: list[ScrapedEvent]) -> Database:
    db = Database(settings.database.url)
    db.create_schema()
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        upsert_events(session, [e for e in map(enricher.enrich, events) if e])
    return db


def test_retention_keeps_yesterday_onwards(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(UTC)
    db = seed(settings, [event("old", now - timedelta(days=4)), event("yesterday", now - timedelta(hours=26)),
                         event("future", now + timedelta(days=2))])  # fmt: skip

    removed = purge_expired(db, MediaStore(settings.media, db), settings.retention, settings.timezone)

    with db.session() as session:
        remaining = {row.id for row in session.query(EventRow)}
        assert session.query(ImageRow).count() == 2  # artwork rows of the purged event are gone too
    assert removed == 1
    assert remaining == {"test:yesterday", "test:future"}


def test_api_filters_categories_and_stored_image(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("a", future), event("b", future, title="Hip hop night")])
    with db.session() as session:
        image = session.query(ImageRow).filter(ImageRow.event_id == "test:a").one()
        image.data, image.content_type, image.downloaded_at = b"\x89PNG", "image/png", datetime.now(UTC)
        image_id = image.id

    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events", params={"genre": "techno", "entry_type": "free_and_paid"}).json()
        assert [e["id"] for e in page["items"]] == ["test:a"]

        detail = client.get("/events/test:a").json()
        assert [t["kind"] for t in detail["tickets"]] == ["free", "paid"]
        assert detail["images"][0]["stored_url"] == f"/images/{image_id}"

        stored = client.get(f"/images/{image_id}")
        assert stored.status_code == 200 and stored.content == b"\x89PNG"

        categories = client.get("/categories").json()
        assert {"value": "free_and_paid", "events": 2} in categories["entry_types"]
        assert client.get("/sources").status_code == 200
