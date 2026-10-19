"""Scraper contract and per-source transport.

Every source owns one subclass of :class:`BaseScraper`, its own settings model and
its own HTTP client instance. Nothing here knows about any specific site; the
only things shared are the retry/rate-limit transport and the output model.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any, ClassVar, Self, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent

T = TypeVar("T")
R = TypeVar("R")

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class SourceSettings(BaseModel):
    """Settings every source block must provide. Sources extend this model."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cron: str = Field(..., description="Crontab expression (5 fields) in the app timezone")
    # Per-source overrides of the global `http` block.
    http: dict[str, Any] = Field(default_factory=dict)


class SourceBlockedError(RuntimeError):
    """The site refused automated access (e.g. a bot-protection checkpoint).

    Never retried or worked around: the run stops and is recorded as ``blocked``
    until the site owner grants access (allow-rule, API key, feed).
    """


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


class BaseScraper[S: SourceSettings](ABC):
    name: ClassVar[str]
    settings_model: ClassVar[type[SourceSettings]]

    def __init__(self, settings: S, http: HttpSettings, timezone: str) -> None:
        self.settings = settings
        self.http = HttpSettings.model_validate(http.model_dump() | settings.http)
        self.timezone = timezone
        self.log = logging.getLogger(f"partylinks.scrapers.{self.name}")
        self.failures: list[str] = []
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(self.http.concurrency)

    # ----------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            timeout=self.http.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.http.user_agent,
                "Accept-Language": self.http.accept_language,
                **self.default_headers(),
                **self.http.headers,
            },
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def is_blocked(self, response: httpx.Response) -> bool:
        """Sources override this to recognise their site's access-denied responses."""
        return False

    def default_headers(self) -> dict[str, str]:
        """Extra headers for this source's client (e.g. Origin/Referer)."""
        return {}

    @abstractmethod
    def scrape(self) -> AsyncIterator[ScrapedEvent]:
        """Yield every event currently listed by the source, following all pagination."""

    # ----------------------------------------------------------------- transport
    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        async with self._semaphore:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.http.max_retries + 1),
                wait=wait_exponential(multiplier=self.http.backoff_seconds, max=60),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    if self.http.request_delay_seconds:
                        await asyncio.sleep(self.http.request_delay_seconds)
                    response = await self._client.request(method, url, **kwargs)
                    if self.is_blocked(response):
                        raise SourceBlockedError(f"{self.name}: access denied by the site ({response.status_code})")
                    response.raise_for_status()
        return response

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        return (await self.request("POST", url, **kwargs)).json()

    async def get_text(self, url: str, **kwargs: Any) -> str:
        return (await self.request("GET", url, **kwargs)).text

    # ----------------------------------------------------------------- helpers
    async def paginate(
        self,
        fetch_page: Callable[[int], Awaitable[tuple[list[T], int | None]]],
        page_size: int,
        max_pages: int,
    ) -> list[T]:
        """Collect every page of a listing, fetching pages in parallel.

        ``fetch_page(index)`` returns ``(items, total)``; ``total`` may be None when the
        API does not report it. With a known total all remaining pages are requested
        at once; otherwise pages are fetched in windows of ``concurrency`` until a
        short or empty page marks the end ("load more" exhausted).
        """
        first, total = await fetch_page(0)
        items = list(first)
        if len(first) < page_size:
            return items
        if total is not None:
            last = min(max_pages, -(-total // page_size))
            for batch, _ in await asyncio.gather(*(fetch_page(i) for i in range(1, last))):
                items.extend(batch)
            return items
        window = self.http.concurrency
        for start in range(1, max_pages, window):
            pages = await asyncio.gather(*(fetch_page(i) for i in range(start, min(start + window, max_pages))))
            for batch, _ in pages:
                items.extend(batch)
            if any(len(batch) < page_size for batch, _ in pages):
                break
        return items

    async def map_isolated(
        self, items: Iterable[T], fn: Callable[[T], Awaitable[R]], label: Callable[[T], str]
    ) -> list[R]:
        """Run ``fn`` over ``items`` concurrently; one failing item never aborts the rest."""

        async def guarded(item: T) -> R | None:
            try:
                return await fn(item)
            except SourceBlockedError:
                raise  # the whole source is refused: stop, don't count it per item
            except Exception as exc:  # noqa: BLE001 - isolate per-item failures
                message = f"{label(item)}: {type(exc).__name__}: {exc}"
                self.failures.append(message)
                self.log.warning("skipped %s", message)
                return None

        results = await asyncio.gather(*(guarded(item) for item in items))
        return [result for result in results if result is not None]
