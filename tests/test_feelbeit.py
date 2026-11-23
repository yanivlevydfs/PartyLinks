"""FeelBeit (LAYERS festival) parser test on real layers_event posts from its wp-json (November 2026)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import feelbeit

FIXTURE = Path(__file__).parent / "fixtures" / "feelbeit" / "events.json"
POSTS = {p["id"]: p for p in json.loads(FIXTURE.read_text(encoding="utf-8"))}
TZ = "Asia/Jerusalem"
CFG = feelbeit.FeelBeitSettings.model_validate(Settings().sources["feelbeit"])


def test_hebrew_post_is_the_show() -> None:
    show = feelbeit.parse_post(POSTS[47], CFG, TZ)
    assert show is not None
    assert (show.source_event_id, show.title) == ("47", "שורשים מעבר לזמן")
    assert show.start_at == datetime(2026, 11, 26, 19, 0, tzinfo=ZoneInfo(TZ))  # the post's date is the start
    assert str(show.url) == "https://feelbeit.com/layers2026/he/events/47/"
    assert (show.venue_name, show.city, show.latitude) == ("FeelBeit", "ירושלים", 31.7618346)
    assert show.description and "נור דרוויש" in show.description
    assert show.images and show.images[0].startswith("https://feelbeit.com/layers2026/wp-content/uploads/")
    assert [(t.price, t.is_free) for t in show.tickets] == [(0, True)] and show.min_price == 0
    # The English and Arabic copies of the same show are not kept.
    assert feelbeit.parse_post(POSTS[44], CFG, TZ) is None and feelbeit.parse_post(POSTS[46], CFG, TZ) is None


def test_dj_set_genres_and_bad_posts() -> None:
    dj = feelbeit.parse_post(POSTS[139], CFG, TZ)
    assert dj is not None and dj.title == "Dance Me to the End of the World"
    assert dj.start_at == datetime(2026, 11, 28, 22, 30, tzinfo=ZoneInfo(TZ))
    enriched = Enricher(Settings().enrichment, TZ).enrich(dj)
    assert enriched is not None and "festival" in enriched.genres
    assert feelbeit.parse_post({**POSTS[47], "date": "soon"}, CFG, TZ) is None
    assert feelbeit.parse_post({**POSTS[47], "status": "draft"}, CFG, TZ) is None


def test_every_page_is_read_to_the_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 posts a page: pages 1-2 are full, page 3 is short and the last; the Hebrew shows of all three are kept."""
    scraper = feelbeit.FeelBeitScraper(CFG.model_copy(update={"per_page": 2}), HttpSettings(), TZ)
    posts = list(POSTS.values())
    calls: list[str] = []

    async def get_json(url: str, **_: object) -> Any:
        calls.append(url)
        page = int(url.split("&page=")[1].split("&")[0])
        return posts[(page - 1) * 2 : page * 2]

    scraper.get_json = get_json  # type: ignore[method-assign]
    monkeypatch.setattr(feelbeit, "is_current", lambda *_: True)

    async def collect() -> list[str]:
        async with scraper:
            return [e.source_event_id async for e in scraper.scrape()]

    assert sorted(asyncio.run(collect())) == ["102", "139", "47"] and not scraper.failures
    assert len(calls) == 3 and "per_page=2&page=3" in calls[-1]
