"""arenas: a Tickchak LIVE venue page's own event slider to its count; its city-wide loops and preloaded events stay out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from partylinks.config import HttpSettings
from partylinks.scrapers.tickchak import ArenasScraper, ArenasSettings

API = "https://api-live.tickchak.co.il"
# A venue page as the site serves it (Menora, 23.11.2026), trimmed: its own slider (3), the promoted one (2,
# a subset of 3), a loop of the city's category sliders (11) and ~100 preloaded events of other venues.
STATE = {
    "page": {
        "breadcrumbs": [{"type": "venue", "id": 1, "slug": "tel-aviv-menorah-hall"}],
        "pageDesign": {"sections": [
            {"sectionId": 2, "type": "promotedSlider"},
            {"sectionId": 3, "type": "eventSlider"},
            {"sectionId": 11, "type": "loopSections", "content": "event"},
        ]},
        "sectionsData": {"loopSections": {"11": {"sliders": [{"id": 1, "url": "shows"}, {"id": 2, "url": "standup"}]}}},
    },
    "data": {"event": {"all": {"x1": {"entityId": "x1", "title": "elsewhere in the city"}}}},
}  # fmt: skip
PAGE = (
    '<html><body><script id="__NEXT_DATA__" type="application/json">'
    + json.dumps({"props": {"pageProps": {"initialState": STATE}}})
    + "</script></body></html>"
)


def _scraper(slugs: list[str]) -> ArenasScraper:
    from partylinks.config import Settings

    raw = {**(Settings().sources["arenas"] or {}), "venue_slugs": slugs}
    return ArenasScraper(ArenasSettings.model_validate(raw), HttpSettings(), "UTC")


def test_the_venue_slider_is_read_to_its_count_and_the_loops_are_not(monkeypatch: Any) -> None:
    scraper = _scraper(["tel-aviv-menorah-hall", "tel-aviv-menorah-hall"])
    assert scraper.page_paths() == ["tel-aviv-menorah-hall"]  # a slug listed twice is read once
    asked: list[tuple[str, dict[str, Any]]] = []

    async def get_text(url: str, **_: Any) -> str:
        assert url == "https://live.tickchak.co.il/tel-aviv-menorah-hall"
        return PAGE

    async def get_json(url: str, *, params: list[tuple[str, Any]], **_: Any) -> dict[str, Any]:
        query = dict(params)
        asked.append((url, query))
        if url == f"{API}/sectionData/3":  # 25 events: two pages of 20
            first = query["offset"]
            ids = range(first, min(first + query["limit"], 25))
            return {"count": 25, "events": [{"entityId": f"e{i}", "title": f"show {i}"} for i in ids]}
        return {"count": 0, "customEvents": []}

    monkeypatch.setattr(scraper, "get_text", get_text)
    monkeypatch.setattr(scraper, "get_json", get_json)
    items = asyncio.run(scraper._page_items("tel-aviv-menorah-hall"))
    assert sorted(items) == sorted(f"e{i}" for i in range(25))  # no preloaded event of another venue
    assert sorted({u for u, _ in asked}) == [f"{API}/customEventsNew", f"{API}/sectionData/3"]
    assert sorted(q["offset"] for u, q in asked if u.endswith("/3")) == [0, 20]
    assert all(q["pageHierarchy[0][type]"] == "venue" and q["pageHierarchy[0][id]"] == 1 for _, q in asked)
