"""A source's request pace, set by how the site answers: slower when it throttles us, faster when it doesn't.

Each source starts at its configured ``concurrency`` and ``request_delay_seconds``. A 429 / 503 halves
the requests in flight and doubles the pause (up to ``pace.max_delay_seconds``); every ``pace.up_after``
answers in a row without one, the pause halves back towards the configured one and then one more request
may run at once, up to ``pace.max_concurrency`` (the configured concurrency when 0). The pace lives for
one run; the next run starts from the configured values again.

Before (Nov 2026) a throttled source kept its pace, backed off, switched proxies and stopped "blocked";
the fix was a hand-set concurrency / delay per source (planet, cinemacity, shotgun in one day).
"""

from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel, ConfigDict, Field


class PaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Never slower than this between two requests, however often the site throttles.
    max_delay_seconds: float = Field(10.0, gt=0)
    # The first slow-down's pause when the configured one is 0.
    min_delay_seconds: float = Field(0.25, gt=0)
    # Answers in a row without a throttle before one step faster.
    up_after: int = Field(50, ge=1)
    # The most requests at once when speeding up; 0: the configured concurrency (never above it).
    max_concurrency: int = Field(0, ge=0)
    # A source going direct (proxied: false) that the site still throttles at the slowest pace moves to
    # `http.source_proxy`: Shotgun rationed Railway's IP to one page per 10 s, 90 min without a save
    # (2026-11-11), when through the rotating proxy each page is ~3 s, several at once.
    proxy_when_slowest: bool = True


class Pace:
    """Gate and pause for one source's requests: ``async with pace:`` around each request (it waits for a
    slot and then for the pause), ``throttled()`` on a 429 / 503, ``answered()`` on any other answer."""

    def __init__(self, concurrency: int, delay: float, settings: PaceSettings, log: logging.Logger) -> None:
        self.settings = settings
        self.log = log
        self.base_delay = delay
        self.delay = delay
        self.top = max(settings.max_concurrency or concurrency, 1)
        self.limit = min(concurrency, self.top)
        self._busy = 0
        self._streak = 0
        self._slowed_at = -1e9
        self._free = asyncio.Condition()

    async def __aenter__(self) -> None:
        async with self._free:
            await self._free.wait_for(lambda: self._busy < self.limit)
            self._busy += 1
        if self.delay:
            await asyncio.sleep(self.delay)

    async def __aexit__(self, *exc_info: object) -> None:
        async with self._free:
            self._busy -= 1
            self._free.notify_all()

    def throttled(self) -> None:
        self._streak = 0
        # Requests in flight together get their 429s together: one slow-down for the burst, not one each.
        now = time.monotonic()
        if not self.settings.enabled or now - self._slowed_at < max(self.delay, self.settings.min_delay_seconds):
            return
        self._slowed_at = now
        before = (self.limit, self.delay)
        self.limit = max(1, self.limit // 2)
        self.delay = min(self.settings.max_delay_seconds, max(self.delay * 2, self.settings.min_delay_seconds))
        if (self.limit, self.delay) != before:
            self.log.warning("pace: throttled by the site, slower: %d at once, %.2fs apart", self.limit, self.delay)

    @property
    def slowest(self) -> bool:
        return self.settings.enabled and self.limit == 1 and self.delay >= self.settings.max_delay_seconds

    def answered(self) -> None:
        if not self.settings.enabled:
            return
        self._streak += 1
        if self._streak < self.settings.up_after:
            return
        self._streak = 0
        if self.delay > self.base_delay:
            self.delay = max(self.base_delay, self.delay / 2)
        elif self.limit < self.top:
            self.limit += 1
            asyncio.get_running_loop().call_soon(self._wake)
        else:
            return
        self.log.info("pace: %d answers without a throttle, faster: %d at once, %.2fs apart", self.settings.up_after, self.limit, self.delay)

    def _wake(self) -> None:
        async def notify() -> None:
            async with self._free:
                self._free.notify_all()

        asyncio.ensure_future(notify())
