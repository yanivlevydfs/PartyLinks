"""Categories of the newer sources that once mapped to no site genre (Nov 2026 audit)."""

from datetime import UTC, datetime

import pytest

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent


@pytest.mark.parametrize(
    ("category", "genre"),
    [
        ("Weitere Konzerte", "live_show"),
        ("Klassik", "classical_opera"),
        ("Kinderveranstaltung", "kids"),
        ("Hard & Heavy", "metal"),
        ("Tanz", "dance"),
        ("NBA", "sport"),
        ("ליגה אנגלית", "sport"),
        ("התנדבות", "volunteering"),
        ("סרט", "cinema"),
        ("גל חדש", "rock_indie"),
        ("ستاند اب كوميدي", "stand_up"),
    ],
)
def test_category_reaches_its_genre(category: str, genre: str) -> None:
    settings = get_settings()
    event = ScrapedEvent(
        source="test",
        source_event_id="1",
        url="https://example.com/1",
        title="qqq",
        start_at=datetime(2030, 1, 1, tzinfo=UTC),
        source_genres=[category],
    )
    assert genre in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)
