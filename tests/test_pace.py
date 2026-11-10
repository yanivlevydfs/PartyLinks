"""A source slows down when the site throttles it and speeds up again when it stops (partylinks.pace)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from partylinks.config import HttpSettings
from partylinks.pace import Pace, PaceSettings
from partylinks.scrapers.base import BaseScraper, SourceSettings

LOG = logging.getLogger("test")


def test_throttled_halves_the_requests_and_doubles_the_pause_up_to_the_cap() -> None:
    pace = Pace(8, 0.0, PaceSettings(max_delay_seconds=1.0, min_delay_seconds=0.01), LOG)
    pace.throttled()
    pace.throttled()  # the same burst's second 429: no second slow-down
    assert (pace.limit, pace.delay) == (4, 0.01)  # no pause configured: the first one is min_delay_seconds
    for _ in range(10):
        pace._slowed_at = -1e9  # a later burst
        pace.throttled()
    assert (pace.limit, pace.delay) == (1, 1.0)  # never under 1 at once, never over the cap


def test_a_streak_of_answers_brings_the_pause_back_then_the_concurrency() -> None:
    async def run() -> Pace:
        pace = Pace(4, 0.5, PaceSettings(up_after=3), LOG)
        pace.throttled()
        pace._slowed_at = -1e9
        pace.throttled()
        assert (pace.limit, pace.delay) == (1, 2.0)
        steps = []
        for _ in range(4 * 3):
            for _ in range(3):
                pace.answered()
            steps.append((pace.limit, pace.delay))
        assert steps[:3] == [(1, 1.0), (1, 0.5), (2, 0.5)]  # the pause first, down to the configured one
        return pace

    pace = asyncio.run(run())
    assert (pace.limit, pace.delay) == (4, 0.5)  # back to the configured pace, never above it


def test_the_gate_holds_no_more_requests_than_the_limit() -> None:
    async def run() -> int:
        pace = Pace(2, 0.0, PaceSettings(), LOG)
        busy = top = 0

        async def one() -> None:
            nonlocal busy, top
            async with pace:
                busy += 1
                top = max(top, busy)
                await asyncio.sleep(0.01)
                busy -= 1

        await asyncio.gather(*(one() for _ in range(10)))
        return top

    assert asyncio.run(run()) == 2


def test_disabled_keeps_the_configured_pace() -> None:
    pace = Pace(4, 0.5, PaceSettings(enabled=False), LOG)
    pace.throttled()
    assert (pace.limit, pace.delay) == (4, 0.5)


class _Probe(BaseScraper[SourceSettings]):
    name = "probe"
    settings_model = SourceSettings

    async def scrape(self):  # type: ignore[override]
        if False:
            yield


def test_a_scraper_slows_down_on_429_and_still_gets_the_page() -> None:
    answers = iter([429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers))

    http = HttpSettings(concurrency=4, request_delay_seconds=0, backoff_seconds=0, pace=PaceSettings(min_delay_seconds=0.01))
    scraper = _Probe(SourceSettings(cron="0 * * * *"), http, "UTC")

    async def run() -> Any:
        scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return await scraper.request("GET", "https://example.com/")

    assert asyncio.run(run()).status_code == 200
    assert (scraper._pace.limit, scraper._pace.delay) == (2, 0.01)
