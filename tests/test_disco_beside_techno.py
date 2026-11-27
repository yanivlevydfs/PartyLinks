"""Go-Out's "דיסקו" beside "טכנו" on a techno night is dropped (declared_dropped_beside); Hebrew "פאנק" is not funk."""

from __future__ import annotations

from pydantic import HttpUrl

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent


def _genres(title: str, description: str = "", declared: list[str] | None = None) -> list[str]:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    return enricher._detect_genres(
        ScrapedEvent.model_construct(
            source="goout",
            source_event_id="1",
            url=HttpUrl("https://example.com/e"),
            title=title,
            description=description,
            source_genres=declared or [],
            event_types=[],
        )
    )


def test_disco_beside_techno_dropped_unless_named() -> None:
    assert "disco_funk" not in _genres("ROOM 48 X BEAT BOUTIQUE | 3.12", "DJ Zombi, Narkis", ["טכנו", "דיסקו"])
    assert "techno" in _genres("ROOM 48 X BEAT BOUTIQUE | 3.12", "DJ Zombi, Narkis", ["טכנו", "דיסקו"])
    assert "disco_funk" in _genres("Nu Disco & Techno", "", ["טכנו", "דיסקו"])  # the title names it
    assert "disco_funk" in _genres("Night", "", ["דיסקו"])  # alone, as before


def test_hebrew_punk_is_not_funk() -> None:
    assert "disco_funk" not in _genres("שש שש שש + מהר וכואב", "ערב פאנק במועדון")
    assert "disco_funk" in _genres("מסיבת פאנקי", "")
