""" "hardcore" is hardstyle only beside electronic genres and never beside rock / punk (declared_genres_beside)."""

from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent


def test_hardcore_is_hardstyle_only_at_a_rave() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)

    def genres(description: str, declared: list[str] | None = None) -> list[str]:
        return enricher._detect_genres(
            ScrapedEvent.model_construct(
                source="shotgun", title="Night", description=description, source_genres=declared or [], event_types=[]
            )
        )

    assert "hardstyle" in genres("Genres: hard techno | industrial | hardcore | hard bounce")
    assert "hardstyle" in genres("des kicks sous tension, entre hardcore, break et dubstep énervé")
    assert "hardstyle" not in genres("Monde de Merde - punk hardcore - France")
    assert "hardstyle" not in genres("Utopeças (Floripa), emo, post-hardcore e math rock")
    assert "hardstyle" not in genres("the hardcore fans of the show")  # no electronic genre beside it
    assert "hardstyle" in genres("", ["hardcore"])  # a source's own category, as before
