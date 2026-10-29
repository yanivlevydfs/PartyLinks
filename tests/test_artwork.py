"""Drawn cards for events with no picture: only then, never while a picture is coming, gone once one is
stored; the event's name is on the card; served as WebP; cards, retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from partylinks.api import create_app
from partylinks.artwork import draw, service
from partylinks.artwork.models import EventArtworkRow
from partylinks.db import EventRow, ImageRow, delete_events
from tests.test_storage_api import event, seed, settings_for

SOON = datetime.now(UTC) + timedelta(days=2)


def _settings(tmp_path: Path):
    s = settings_for(tmp_path)
    s.security.rate_limit_per_minute = s.security.data_rate_limit_per_minute = 100_000
    return s


def test_only_events_with_no_picture_get_a_card(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [
        event("bare", SOON, title="מסיבת טכנו בלי תמונה", images=[]),  # nothing: a card
        event("coming", SOON + timedelta(hours=1), title="Picture on its way"),  # found, not stored yet: wait
    ])  # fmt: skip
    assert service.run(settings, db)["drawn"] == 1
    with db.session() as s:
        assert list(s.scalars(select(EventArtworkRow.event_id))) == ["test:bare"]
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        cards = {e["id"]: e["cover_url"] for e in c.get("/events").json()["items"]}
        assert cards["test:bare"].startswith("/artwork/") and cards["test:coming"] is None
        got = c.get(cards["test:bare"] + "/640.webp")
        assert got.status_code == 200 and got.headers["content-type"] == "image/webp" and got.content[:4] == b"RIFF"
        assert c.get(cards["test:bare"] + "/333.webp").status_code == 404


def test_a_real_picture_wins_and_the_card_goes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [event("bare", SOON, images=[])])
    service.run(settings, db)
    with db.session() as s:  # the scraper now brings and stores a picture
        s.add(ImageRow(event_id="test:bare", position=0, url="https://example.com/x.jpg", local_path="x.jpg",
                       content_type="image/jpeg", downloaded_at=datetime.now(UTC)))  # fmt: skip
    assert service.run(settings, db)["removed"] == 1
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(EventArtworkRow)) == 0
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/events").json()["items"][0]["cover_url"].startswith("/images/")


def test_a_failed_download_gets_a_card_and_changes_redraw_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [event("broken", SOON)])
    with db.session() as s:  # its picture could not be fetched: no picture after all
        s.execute(update(ImageRow).values(error="404"))
    assert service.run(settings, db)["drawn"] == 1
    assert service.run(settings, db)["drawn"] == 0  # unchanged: not drawn again
    with db.session() as s:
        s.execute(update(EventRow).where(EventRow.id == "test:broken").values(title="A new name"))
    assert service.run(settings, db)["drawn"] == 1
    with db.session() as s:
        delete_events(s, ["test:broken"])  # retention: the card goes with its event
        assert s.scalar(select(func.count()).select_from(EventArtworkRow)) == 0


def test_the_card_shows_the_name_stores_and_english_days() -> None:
    img = draw.draw(event_id="x:1", title="Founders Running Club :: Tel Aviv", sources=["eventbrite", "meetup"],
                    start_at=datetime(2026, 10, 31, 7, tzinfo=UTC), city="Tel Aviv")  # fmt: skip
    assert img.size == (1280, 960)
    he = draw.draw(event_id="x:2", title="מסיבת טכנו גדולה בבארבי עם אורחים מחו\"ל ועוד הרבה מילים שלא נכנסות",
                   sources=["goout", "eventer", "zygo", "tickchak", "ogo", "barby"], start_at=None, city=None)  # fmt: skip
    assert he.size == (1280, 960)
    assert draw.store_names()["goout"] == "Go-Out"  # the website's names (web/src/sourceNames.json)
    assert (
        draw.draw(event_id="x:1", title="A", sources=[], start_at=None, city=None).tobytes()
        == draw.draw(event_id="x:1", title="A", sources=[], start_at=None, city=None).tobytes()
    )  # same event, same look


def test_a_picture_arriving_while_drawing_wins(tmp_path: Path, monkeypatch) -> None:
    """The save re-checks inside its transaction: a picture stored mid-draw means no card is written."""
    settings = _settings(tmp_path)
    db = seed(settings, [event("bare", SOON, images=[])])
    real_draw = draw.draw

    def draw_then_picture_arrives(**kw):
        img = real_draw(**kw)
        with db.session() as s:
            s.add(ImageRow(event_id="test:bare", position=0, url="https://example.com/x.jpg", local_path="x.jpg",
                           content_type="image/jpeg", downloaded_at=datetime.now(UTC)))  # fmt: skip
        return img

    monkeypatch.setattr(draw, "draw", draw_then_picture_arrives)
    assert service.run(settings, db)["drawn"] == 0
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(EventArtworkRow)) == 0


def test_cards_are_drawn_concurrently_and_one_failure_spares_the_rest(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    db = seed(settings, [event(f"e{i}", SOON + timedelta(hours=i), title=f"Event {i}", images=[]) for i in range(6)])
    real_draw = draw.draw

    def sometimes_fails(**kw):
        if kw["event_id"] == "test:e3":
            raise ValueError("bad font day")
        return real_draw(**kw)

    monkeypatch.setattr(draw, "draw", sometimes_fails)
    assert service.run(settings, db)["drawn"] == 5
    monkeypatch.setattr(draw, "draw", real_draw)
    assert service.run(settings, db)["drawn"] == 1  # the failed one, next pass
