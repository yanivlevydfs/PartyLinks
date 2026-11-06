"""Decade genres (/genre/eighties, /genre/nineties): sub-genres of throwback, read from Hebrew and English."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.seo import genre_label
from tests.test_storage_api import event, seed, settings_for


def test_decades_are_throwback_sub_genres() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)

    def genres(title: str) -> list[str]:
        return enricher._detect_genres(
            ScrapedEvent.model_construct(source="x", title=title, description="", source_genres=[], event_types=[])
        )

    assert {"eighties", "throwback"} <= set(genres("מסיבת שנות ה-80 הגדולה"))
    assert {"nineties", "throwback"} <= set(genres("DAVE PEARCE 90s DANCE ANTHEMS"))
    assert "eighties" in genres("ליין האייטיז של תל אביב") and "nineties" not in genres("ליין האייטיז של תל אביב")
    # The throwback page keeps them.
    assert {"eighties", "nineties"} <= set(settings.enrichment.expand_genres(["throwback"]))
    assert genre_label("eighties", "en") == "80s" and "80" in genre_label("eighties", "he")


def test_decade_page_lists_its_events_and_links_its_parent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    night = datetime.combine(datetime.now(tz).date(), time(23, 0), tzinfo=tz) + timedelta(days=1)
    seed(settings, [event(str(i), night + timedelta(days=i), title=f"מסיבת שנות ה-80 {i}") for i in range(3)])
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/genre/eighties")
        assert page.status_code == 200 and "מסיבת שנות ה-80 0" in page.text and 'hreflang="en"' in page.text
        assert 'href="/genre/throwback"' in page.text  # the umbrella page
        assert 'href="/genre/eighties"' in client.get("/genre/throwback").text  # and back
        assert "/genre/eighties" in client.get("/sitemap-1.xml").text
        assert client.get("/genre/nineties").status_code == 410  # no events: no thin page
