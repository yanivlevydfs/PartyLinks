"""OGO abroad (us / de hosts): country, zone and source on top of the shared OGO parser (no network)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ogo_abroad import OgoAbroadSettings, OgoUsScraper, parse_event, upcoming_event_urls, us_zone

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


def test_scrape_keeps_urls_dated_before_today() -> None:
    # A weekly class's URL keeps its first date (May); the page is the next session.
    url = f"{US}/new-york/2026/05/29/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"
    settings = OgoAbroadSettings(
        cron="0 * * * *",
        site_url="https://us.ogo.events",
        sitemap_path="/sitemap.xml",
        days_ahead=730,
        country="US",
        default_zone="America/New_York",
        currency="USD",
    )
    scraper = OgoUsScraper(settings, HttpSettings(), "America/New_York")
    pages = {
        "https://us.ogo.events/sitemap.xml": "<loc>https://us.ogo.events/sitemap/0.xml</loc>",
        "https://us.ogo.events/sitemap/0.xml": f"<loc>{url.replace('/he/', '/en/')}</loc>",
        url: _read("event.html"),
    }

    async def fake_get_text(url: str, **_: object) -> str:
        return pages[url]

    scraper.get_text = fake_get_text  # type: ignore[method-assign]

    async def run() -> list[str]:
        return [e.source_event_id async for e in scraper.scrape()]

    assert asyncio.run(run()) == ["xnGgs5FWzgbbqgweE227fm"]


def test_enrich_gives_site_genre() -> None:
    url = f"{US}/new-york/2026/11/16/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"
    event = parse_event(_read("event.html"), url, OgoUsScraper.name, "America/New_York")
    assert event is not None
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None and enriched.genres


def test_us_zone_by_state() -> None:
    ny = "America/New_York"
    # The URL's state suffix, then the address's state; never the country's main zone (Chicago).
    assert us_zone("long beach ca", None, ny) == "America/Los_Angeles"
    assert us_zone("tenafly", "(Kaplen JCC) 411 E Clinton Ave, Tenafly, NJ 07670", ny) == ny
    assert us_zone("st petersburg fl", "300 58th St. N., St. Petersburg, FL, USA", ny) == ny
    assert us_zone("pasadena", "Temple, 1434 N Altadena Dr, Pasadena, CA 91107, USA", ny) == "America/Los_Angeles"
    assert us_zone("pasadena", "Los Angeles County, California, Pasadena", ny) == "America/Los_Angeles"
    assert us_zone("tarrytown", "Тарритаун", ny) == ny  # a town GeoNames does not list: the default
    assert us_zone("any city", None, ny) == ny


def test_russian_tags_are_translated_not_russian_language() -> None:
    from partylinks.scrapers.ogo import _TAGS

    # OGO's tags are Russian on every locale: untranslated, these matched the Russian-language genre.
    assert _TAGS["праздник"] == "holidays" and _TAGS["фестиваль"] == "festival"
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    url = f"{US}/new-york/2026/11/16/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"
    event = parse_event(_read("event.html"), url, OgoUsScraper.name, "America/New_York")
    assert event is not None
    enriched = enricher.enrich(event.model_copy(update={"source_genres": ["holidays", "festival"]}))
    assert enriched is not None and "russian" not in enriched.genres
    assert {"holidays", "festival"} <= set(enriched.genres)
    # No priced tier abroad: the country's currency, not the shekel default.
    de_url = "https://de.ogo.events/he/event/de/berlin/2026/11/13/eli-yatzp-new-show-f5hknqanjcCgQRBMAvBs8Z"
    unpriced = parse_event(_read("event_date_only.html"), de_url, "ogo_de", "Europe/Berlin", "EUR")
    assert unpriced is not None and not unpriced.tickets and unpriced.currency == "EUR"
