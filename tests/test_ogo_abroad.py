"""OGO abroad (us / de hosts): country, zone and source on top of the shared OGO parser (no network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ogo_abroad import OgoUsScraper, parse_event, upcoming_event_urls

FIXTURES = Path(__file__).parent / "fixtures" / "ogo"
US = "https://us.ogo.events/he/event/us"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_event_abroad() -> None:
    url = f"{US}/new-york/2026/11/16/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"
    event = parse_event(_read("event.html"), url, OgoUsScraper.name, "America/New_York")

    assert event is not None
    assert (event.source, str(event.url), event.country) == ("ogo_us", url, "US")
    assert event.time_zone == "America/New_York"
    assert event.source_event_id == "xnGgs5FWzgbbqgweE227fm"
    assert event.city == "new york"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-16T19:30:00+00:00"
    assert event.source_genres == ["stand up"]


def test_date_only_start_is_local_midnight() -> None:
    url = "https://de.ogo.events/he/event/de/berlin/2026/11/13/eli-yatzp-new-show-f5hknqanjcCgQRBMAvBs8Z"
    event = parse_event(_read("event_date_only.html"), url, "ogo_de", "Europe/Berlin")

    assert event is not None and event.country == "DE" and event.time_zone == "Europe/Berlin"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-13T00:00:00+01:00"


def test_upcoming_event_urls_any_country() -> None:
    sitemap = "".join(
        f"<loc>{u}</loc>"
        for u in [
            "https://us.ogo.events/ru/event/us/miami/2026/12/02/b-show-AAA111",
            "https://us.ogo.events/en/event/us/chicago/2026/11/20/a-show-BBB222",
            "https://us.ogo.events/ru/event/us/chicago/2025/01/01/old-CCC333",
            "https://us.ogo.events/ru/venue/us/chicago/x",
        ]
    )
    assert upcoming_event_urls([sitemap], "he", date(2026, 11, 1), date(2026, 12, 31)) == [
        f"{US}/chicago/2026/11/20/a-show-BBB222",
        f"{US}/miami/2026/12/02/b-show-AAA111",
    ]


def test_enrich_gives_site_genre() -> None:
    url = f"{US}/new-york/2026/11/16/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"
    event = parse_event(_read("event.html"), url, OgoUsScraper.name, "America/New_York")
    assert event is not None
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and enriched.genres
