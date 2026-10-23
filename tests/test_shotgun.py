"""Shotgun: a real event page (Manaus, October 2026) trimmed to its JSON-LD, and the listing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from partylinks.config import HttpSettings
from partylinks.scrapers.shotgun import _EVENT_LINK, ShotgunScraper, ShotgunSettings, links, parse_event

SITE = "https://shotgun.live"
PAGE = (Path(__file__).parent / "fixtures" / "shotgun" / "event_brazil.html").read_text(encoding="utf-8")
URL = f"{SITE}/en/events/intrance-24-10"


def test_event_abroad_is_skipped_and_one_in_israel_is_read() -> None:
    assert parse_event(PAGE, URL, "Asia/Jerusalem") is None  # addressCountry BR
    israel = PAGE.replace('"addressCountry":"BR"', '"addressCountry":"IL"')
    assert israel != PAGE
    event = parse_event(israel, URL, "Asia/Jerusalem")
    assert event is not None and event.source == "shotgun" and event.source_event_id == "intrance-24-10"
    assert event.venue_name == "Nexus Lounge & Club" and event.latitude and event.tickets


def test_listing_links() -> None:
    html = (
        '<a href="/en/events/a-1">x</a><a href="/en/events/a-1">y</a><a href="https://shotgun.live/en/events/b">z</a>'
    )
    assert links(html, _EVENT_LINK, SITE) == [f"{SITE}/en/events/a-1", f"{SITE}/en/events/b"]


def test_no_israeli_pages_yet_is_an_empty_run_not_a_failure() -> None:
    settings = ShotgunSettings(
        cron="0 * * * *",
        site_url=SITE,
        country_path="/en/venues/-/israel",
        city_paths=["/en/cities/tel-aviv"],
        proxied=False,
    )
    scraper = ShotgunScraper(settings, HttpSettings(max_retries=0), "Asia/Jerusalem")
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(404, text="<title>Page not found</title>")

    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]

    async def run() -> list[object]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    assert asyncio.run(run()) == [] and asked == ["/en/venues/-/israel", "/en/cities/tel-aviv"]
