"""skip_title_words: every source's config can leave out classes and courses by title words."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers.base import BaseScraper, SourceSettings

TITLES = ["קורס דיג'יי - 18+ ארבעה מפגשים ומסיבה", "הופעה בפארק", "Yoga COURSE", "חוג קרמיקה"]


class _Listing(BaseScraper[SourceSettings]):
    name = "listing"
    settings_model = SourceSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        for i, title in enumerate(TITLES):
            yield ScrapedEvent(
                source=self.name, source_event_id=str(i), url=f"https://example.com/{i}", title=title,  # pyright: ignore[reportArgumentType]
                start_at=datetime(2026, 12, 1, 20, tzinfo=UTC),
            )  # fmt: skip


def _titles(words: list[str]) -> list[str]:
    async def run() -> list[str]:
        settings = SourceSettings(cron="0 6 * * *", skip_title_words=words)
        async with _Listing(settings, HttpSettings(), "Asia/Jerusalem") as scraper:
            return [e.title async for e in scraper.events()]

    return asyncio.run(run())


def test_every_source_skips_titles_with_its_words() -> None:
    assert _titles(["קורס", "חוג", "course"]) == ["הופעה בפארק"]  # Hebrew, and English in any case


def test_no_words_keeps_everything() -> None:
    assert _titles([]) == TITLES
    assert _titles(["  "]) == TITLES  # a blank word would match every title
