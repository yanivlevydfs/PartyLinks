"""Which picture an event shows: own stored > duplicate's stored > own URL still loading > drawn card;
a failed download or a site logo is no picture (partylinks.pictures, 2026-11-11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import update

from partylinks.api import create_app
from partylinks.db import EventRow, ImageRow
from partylinks.parsing import is_site_logo
from partylinks.seo import event_jsonld
from tests.test_storage_api import event, seed, settings_for

SOON = datetime.now(UTC) + timedelta(days=2)
FAVICON = "https://www.ticketmaster.co.il/favicon.ico"


def test_site_logos_favicons_and_placeholders() -> None:
    for url in (FAVICON, "https://b7t.smarticket.co.il/uploads/no_pic.png",
                "https://showshappening.blob.core.windows.net/content/logos/Round-logo-black.png",
                "https://media.stubhubstatic.com/stubhub-v2-catalog/d_vgg-defaultLogo.jpg/q_auto:low,f_auto",
                "https://www.kfar-saba.muni.il/content/images/w-480-no-pic.png", "https://x.il/content/images/fb.png?v=2025a"):  # fmt: skip
        assert is_site_logo(url), url
    for url in ("https://muzi.co.il/wp-content/uploads/2024/07/6685a979160ea.jpg", "https://x.example/nopicnic.jpg",
                "https://x.example/no-pictures-party-night.jpg", "https://images.makore.co.il/events/24df5bc3",
                "https://media.stubhubstatic.com/stubhub-v2-catalog/d_vgg-defaultLogo.jpg/q_auto:low,f_auto/categories/54350/6561814"):  # fmt: skip
        assert not is_site_logo(url), url


def test_a_scraped_favicon_is_never_kept() -> None:
    e = event("fav", SOON, images=[FAVICON, "https://example.com/poster.jpg"])
    assert (e.image_url, e.images) == ("https://example.com/poster.jpg", ["https://example.com/poster.jpg"])
    assert event("only", SOON, images=[FAVICON]).images == []


def test_a_failed_own_picture_takes_the_duplicates(tmp_path: Path) -> None:
    """Berry Sakharof (tickchak): its only picture, ticketmaster's favicon, never downloaded; 11 duplicates had one."""
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    settings.security.page_rate_limit_per_minute = settings.security.page_rate_limit_per_day = 100_000  # partylinks.page_guard
    db = seed(settings, [event("berry", SOON, images=[])])
    with db.session() as s:
        row = s.get(EventRow, "test:berry")
        assert row is not None
        s.execute(update(EventRow).where(EventRow.id == row.id).values(image_url=FAVICON))  # stored before the check
        s.add(ImageRow(event_id=row.id, position=0, url=FAVICON, error="ValueError: not an image", error_at=datetime.now(UTC)))
        s.add(EventRow(start_at=row.start_at, title=row.title, id="other:berry", source="other", source_event_id="berry",
                       url="https://o.example/berry", group_id=row.group_id, is_primary=False))  # fmt: skip
        s.flush()
        twin = ImageRow(event_id="other:berry", position=0, url="https://o.example/berry.jpg", local_path="b.jpg",
                        content_type="image/jpeg", downloaded_at=datetime.now(UTC))  # fmt: skip
        s.add(twin)
        s.flush()
        twin_url = f"/images/{twin.id}"
        s.expire_all()
        assert event_jsonld(s.get(EventRow, "test:berry"), settings)["image"][0].endswith(twin_url)

    with TestClient(create_app(settings, start_scheduler=False)) as c:
        card = next(e for e in c.get("/events").json()["items"] if e["id"] == "test:berry")
        assert (card["cover_url"], card["image_url"]) == (twin_url, None)
        page = c.get("/events/test:berry").json()
        assert (page["images"], page["image_url"], page["cover_url"]) == ([], None, twin_url)
