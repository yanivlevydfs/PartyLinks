"""Resized WebP copies of the artwork: made when stored, backfilled, served, deleted with the file."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import PIL.features
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

from partylinks import service as service_module
from partylinks.api import create_app
from partylinks.config import HttpSettings, Settings
from partylinks.db import Database, ImageRow, upsert_events
from partylinks.media import MediaStore, has_variants, make_variants, variant_path
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.service import ScrapeService

WEBP = b"WEBP"


def picture(size: tuple[int, int], mode: str = "RGB", fmt: str = "JPEG", **save: Any) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 120, 128)[: len(mode)] if mode != "L" else 128).save(buf, fmt, **save)
    return buf.getvalue()


def settings_for(tmp_path: Path, **media: Any) -> Settings:
    return Settings(
        database={"url": f"sqlite:///{tmp_path / 'v.db'}"},
        scheduler={"enabled": False},
        media={"download": False, "storage": "filesystem", "directory": str(tmp_path / "media")} | media,
        dedup={"enabled": False},
        geocoding={"enabled": True, "max_lookups_per_run": 0},  # never touch the network in tests
    )


def event(eid: str, source: str = "test", **extra: Any) -> ScrapedEvent:
    fields: dict[str, Any] = {
        "source": source, "source_event_id": eid, "url": f"https://e.example/{eid}", "title": f"Party {eid}",
        "start_at": datetime.now(UTC) + timedelta(days=2), "images": [f"https://img.example/{eid}.jpg"],
        "tickets": [TicketTier(name="Entry", price=Decimal(50))],
    }  # fmt: skip
    return ScrapedEvent(**(fields | extra))


def stored(tmp_path: Path, data: bytes, name: str = "test/flyer.jpg") -> tuple[Settings, Database, MediaStore, int]:
    """One event whose artwork file is on disk (no copies yet)."""
    settings = settings_for(tmp_path)
    db = Database(settings.database.url)
    db.create_schema()
    media = MediaStore(settings.media, db)
    media.path_for(name).parent.mkdir(parents=True, exist_ok=True)
    media.path_for(name).write_bytes(data)
    with db.session() as session:
        upsert_events(session, [event("a")])
        session.execute(
            text("UPDATE event_images SET local_path = :p, content_type = 'image/jpeg', downloaded_at = CURRENT_TIMESTAMP"),
            {"p": name},
        )
        image_id = session.query(ImageRow.id).scalar()
    return settings, db, media, image_id


def width_of(path: Path) -> int:
    with Image.open(path) as im:
        assert im.format == "WEBP"
        return im.width


def test_pillow_in_this_environment_writes_webp() -> None:
    assert PIL.features.check("webp")  # the image (requirements.lock) must have it too


class _OneEvent(BaseScraper[SourceSettings]):
    name = "goout"
    settings_model = SourceSettings

    def __init__(self) -> None:
        super().__init__(SourceSettings(cron="0 * * * *"), HttpSettings(), "UTC")

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        yield event("scraped", source="goout")


def test_a_scraped_events_artwork_has_both_copies_right_after_the_run(tmp_path: Path, monkeypatch: Any) -> None:
    settings = settings_for(tmp_path, download=True)
    db = Database(settings.database.url)
    db.create_schema()
    service = ScrapeService(settings, db)
    monkeypatch.setattr(service_module, "build_scraper", lambda name, settings: _OneEvent())

    async def fetch(self: MediaStore, client: Any, url: str) -> tuple[bytes, str]:
        return picture((2000, 1500)), "image/jpeg"

    monkeypatch.setattr(MediaStore, "_fetch", fetch)
    try:
        result = service.start_source("goout", trigger="test").result(timeout=60)
    finally:
        service.shutdown()
    assert result.status == "success"
    with db.session() as session:
        relative = session.query(ImageRow.local_path).scalar()
    original = service.media.path_for(relative)
    assert [width_of(original.with_name(variant_path(original.name, w))) for w in (640, 1280)] == [640, 1280]


def test_copies_keep_alpha_never_upscale_and_follow_exif(tmp_path: Path) -> None:
    png = tmp_path / "logo.png"
    png.write_bytes(picture((300, 200), "RGBA", "PNG"))
    assert make_variants(png, 78)
    with Image.open(png.with_name("logo.png.640.webp")) as im:
        assert im.size == (300, 200) and im.mode == "RGBA"  # small: kept as is, transparency kept

    rotated = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90
    rotated.write_bytes(picture((2000, 1000), exif=exif))
    assert make_variants(rotated, 78)
    with Image.open(rotated.with_name("phone.jpg.640.webp")) as im:
        assert im.size == (640, 1280)  # portrait, as the phone shot it

    cmyk = tmp_path / "print.jpg"
    cmyk.write_bytes(picture((800, 600), "CMYK"))
    assert make_variants(cmyk, 78) and width_of(cmyk.with_name("print.jpg.1280.webp")) == 800


def test_animations_and_broken_files_keep_only_the_original(tmp_path: Path) -> None:
    gif = tmp_path / "loop.gif"
    frames = [Image.new("RGB", (50, 50), c) for c in ("red", "blue")]
    frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff not really a jpeg")
    assert not make_variants(gif, 78) and not make_variants(broken, 78)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.jpg", "loop.gif"]  # no copies, no temp files


def test_backfill_makes_missing_copies_once(tmp_path: Path) -> None:
    _, _, media, _ = stored(tmp_path, picture((1600, 900)))
    assert media.build_missing_variants() == (1, 0)
    assert has_variants(media.path_for("test/flyer.jpg"))
    assert media.build_missing_variants() == (0, 0)  # resumable: done files are skipped


def test_copy_route_serves_webp_immutable_and_falls_back_to_the_original(tmp_path: Path) -> None:
    original = picture((1600, 900))
    settings, _, media, image_id = stored(tmp_path, original)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        settings.media.variant_threads = 0  # (the app's own backfill already ran; this keeps it explicit)
        for path in ("test/flyer.jpg.640.webp", "test/flyer.jpg.1280.webp"):
            media.path_for(path).unlink(missing_ok=True)
        fallback = client.get(f"/images/{image_id}/640.webp")
        assert fallback.status_code == 200 and fallback.content == original
        assert fallback.headers["content-type"] == "image/jpeg"
        assert fallback.headers["cache-control"] == "public, max-age=3600"  # the edge must not keep it a year

        make_variants(media.path_for("test/flyer.jpg"), 78)
        copy = client.get(f"/images/{image_id}/640.webp")
        assert copy.status_code == 200 and copy.content[8:12] == WEBP
        assert copy.headers["content-type"] == "image/webp"
        assert copy.headers["cache-control"] == "public, max-age=31536000, immutable"
        again = client.get(f"/images/{image_id}/640.webp", headers={"if-none-match": copy.headers["etag"]})
        assert again.status_code == 304
        assert client.get(f"/images/{image_id}/999.webp").status_code == 404
        assert client.get(f"/images/{image_id}").content == original  # the original route is unchanged


def test_deleting_a_file_deletes_its_copies_unless_still_used(tmp_path: Path) -> None:
    _, db, media, _ = stored(tmp_path, picture((900, 600)))
    media.build_missing_variants()
    relative = "test/flyer.jpg"
    assert media.delete_files([relative]) == 0 and has_variants(media.path_for(relative))  # still referenced
    with db.session() as session:
        session.execute(text("DELETE FROM event_images"))
    assert media.delete_files([relative]) == 1
    assert not list(media.path_for(relative).parent.iterdir())  # original and both copies gone


def test_move_to_files_makes_copies(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database.url)
    db.create_schema()
    with db.session() as session:
        upsert_events(session, [event("a")])
        session.execute(
            text("UPDATE event_images SET data = :d, content_type = 'image/jpeg', downloaded_at = CURRENT_TIMESTAMP"),
            {"d": picture((1400, 700))},
        )
    media = MediaStore(settings.media, db)
    assert media.move_to_files() == 1
    with db.session() as session:
        relative = session.query(ImageRow.local_path).scalar()
    assert has_variants(media.path_for(relative))


def test_social_previews_keep_the_original_jpeg(tmp_path: Path) -> None:
    settings, _, _, image_id = stored(tmp_path, picture((1600, 900)))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        html = client.get("/event/test:a").text
    assert f'<meta property="og:image" content="{settings.site.base_url}/images/{image_id}" />' in html
    assert ".webp" not in html
