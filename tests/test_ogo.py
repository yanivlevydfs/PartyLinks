"""OGO: sitemap filtering and JSON-LD event parsing on trimmed real pages (no network)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path

import pytest

from partylinks.config import HttpSettings
from partylinks.scrapers import ogo
from partylinks.scrapers.ogo import OgoScraper, OgoSettings, parse_event, upcoming_event_urls

FIXTURES = Path(__file__).parent / "fixtures" / "ogo"
BASE = "https://www.ogo.co.il/he/event/il"
URL = f"{BASE}/tel-aviv/2026/11/16/yonat-barak-on-ur-number-7-world-champion-comedy-xnGgs5FWzgbbqgweE227fm"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_event() -> None:
    event = parse_event(_read("event.html"), URL, "Asia/Jerusalem")

    assert event is not None
    assert event.source == "ogo"
    assert event.source_event_id == "xnGgs5FWzgbbqgweE227fm"
    assert event.title.startswith("יונתן ברק בסיבוב הופעות מספר 7")
    assert event.description and event.description.startswith("יונתן ברק")
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-16T19:30:00+00:00"
    assert event.end_at is None  # 23:59:59.999 local: the site's "until the end of the day"
    assert event.venue_name == "גריי תל אביב"
    assert event.city == "tel aviv"  # from the URL: the JSON-LD's locality is Russian
    assert event.source_genres == ["stand up"]
    assert event.extra["purchase_url"].startswith("https://tickets.grayclub.co.il")
    assert event.extra["language"] == "he"
    # The generated share card is dropped; the cover is the artwork.
    assert event.image_url and "img.makore.app" in event.image_url
    assert not any("opengraph-image" in i for i in event.images)
    assert [(t.price, t.sale_start) for t in event.tickets] == [(Decimal(135), None)]
    assert (event.min_price, event.max_price) == (Decimal(135), Decimal(135))


def test_date_only_event_and_abroad() -> None:
    url = f"{BASE}/haifa/2026/11/13/eli-yatzp-new-show-f5hknqanjcCgQRBMAvBs8Z"
    event = parse_event(_read("event_date_only.html"), url, "Asia/Jerusalem")

    assert event is not None
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-13T00:00:00+02:00"
    assert event.end_at is None
    assert event.city == "haifa"
    assert event.artists == ["אלי יצפאן"] and event.artist_profiles == []
    assert event.tickets == [] and event.min_price is None  # offer without a price
    assert event.extra["tags"] == ["concert", "stand up"]
    assert event.extra["purchase_url"].startswith("https://www.haifa.muni.il/event/")

    abroad = f"{BASE}/any-city/2027/04/15/maccabi-tel-viv-kosner-baskoni-vitoria-gasteiz-crA2Vcg2SWNxoyqENKijis"
    assert parse_event(_read("event_abroad.html"), abroad, "Asia/Jerusalem") is None


def test_upcoming_event_urls() -> None:
    sitemaps = [_read("sitemap_0.xml"), _read("sitemap_2.xml")]
    # Past, beyond the window and non-event pages are left out; duplicates once; soonest first; locale swapped.
    assert upcoming_event_urls(sitemaps, "he", date(2026, 11, 1), date(2026, 12, 1)) == [
        f"{BASE}/haifa/2026/11/13/eli-yatzp-new-show-f5hknqanjcCgQRBMAvBs8Z",
        URL,
    ]


class _Oct23(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return datetime(2026, 10, 23, 12, tzinfo=tz)


def test_scrape_reads_every_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ogo, "datetime", _Oct23)
    settings = OgoSettings(
        cron="0 * * * *", site_url="https://www.ogo.co.il", sitemap_path="/sitemap.xml", days_ahead=365
    )
    scraper = OgoScraper(settings, HttpSettings(), "Asia/Jerusalem")
    pages = {
        "https://www.ogo.co.il/sitemap.xml": _read("sitemap_index.xml"),
        "https://www.ogo.co.il/sitemap/0.xml": _read("sitemap_0.xml"),
        "https://www.ogo.co.il/sitemap/2.xml": _read("sitemap_2.xml"),
        URL: _read("event.html"),
    }

    async def fake_get_text(url: str, **_: object) -> str:
        return pages[url]

    scraper.get_text = fake_get_text  # type: ignore[method-assign]

    async def run() -> list[str]:
        return [e.source_event_id async for e in scraper.scrape()]

    # The two other listed pages have no fixture: recorded as failures, the rest still comes through.
    assert asyncio.run(run()) == ["xnGgs5FWzgbbqgweE227fm"]
    assert len(scraper.failures) == 2
