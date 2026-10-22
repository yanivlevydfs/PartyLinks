"""Scraper contract and per-source transport.

Every source owns one subclass of :class:`BaseScraper`, its own settings model and
its own HTTP client instance. Nothing here knows about any specific site; the
only things shared are the retry/rate-limit transport, the resilient listing /
detail helpers and the output model.

Resilience rules implemented here (so every source gets them):
* transient HTTP errors are retried with backoff, honouring ``Retry-After``;
  a concurrency slot is held only while a request is in flight, not while waiting;
* a body that is not the JSON shape a caller expects raises ``ParseError``;
* a failing listing page or detail item is recorded in ``failures`` and skipped -
  it never discards what other pages/items already returned;
* details stream out as they complete, so the service saves progressively and a
  timeout or block keeps everything collected so far;
* ``SourceBlockedError`` (site refuses automated access) stops the source at once
  and cancels in-flight work.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from typing import Any, ClassVar, Self, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential

from partylinks.artists import ArtistProfile
from partylinks.config import HttpSettings, validate_cron
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, to_int

T = TypeVar("T")
R = TypeVar("R")

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 120


class SourceSettings(BaseModel):
    """Settings every source block must provide. Sources extend this model."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cron: str = Field(..., description="Crontab expression (5 fields) in the app timezone")
    # Per-source overrides of the global `http` block.
    http: dict[str, Any] = Field(default_factory=dict)
    # After a complete, error-free run, hide upcoming events this source no longer lists
    # (cancelled / pulled). Turn off for sources whose listing is not the full catalogue.
    unlist_missing: bool = True
    # Send this source through `http.source_proxy`. On by default: every source goes through it on
    # Railway, where the variable is set; locally it is unset, so requests go direct.
    proxied: bool = True

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        return validate_cron(value)


class SourceBlockedError(RuntimeError):
    """The site refused automated access (e.g. a bot-protection checkpoint).

    Never retried or worked around: the run stops and is recorded as ``blocked``
    until the site owner grants access (allow-rule, API key, feed).
    """


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def _retry_after(exc: BaseException | None) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    seconds = to_int(exc.response.headers.get("Retry-After"))
    return min(float(seconds), MAX_RETRY_AFTER_SECONDS) if seconds is not None and seconds >= 0 else None


async def gather_or_cancel[X](coros: Iterable[Coroutine[Any, Any, X]]) -> list[X]:
    """``asyncio.gather`` that cancels the remaining tasks when one raises."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class BaseScraper[S: SourceSettings](ABC):
    name: ClassVar[str]
    settings_model: ClassVar[type[SourceSettings]]
    # Sells only events abroad: a stored event of this source with no country is abroad too.
    abroad_only: ClassVar[bool] = False

    def __init__(self, settings: S, http: HttpSettings, timezone: str) -> None:
        self.settings = settings
        overrides = dict(settings.http)
        # Headers merge with the global ones instead of replacing them.
        overrides["headers"] = {**http.headers, **HttpSettings.model_validate({"headers": overrides.get("headers", {})}).headers}
        # A proxied source starts on the first URL of `source_proxy` and moves to the next only when
        # the proxy itself fails (never because the site refused us). A per-source proxy wins.
        pool = [p.strip() for p in (http.source_proxy or "").split(",") if p.strip()] if settings.proxied else []
        self._spare_proxies = pool[1:] if pool and "proxy" not in overrides else []
        if pool:
            overrides.setdefault("proxy", pool[0])
        self.http = HttpSettings.model_validate(http.model_dump() | overrides)
        self.timezone = timezone
        self.log = logging.getLogger(f"partylinks.scrapers.{self.name}")
        # Credentials stay out of the log: only the proxy's host.
        self.log.info("via %s", self.http.proxy.rpartition("@")[2] if self.http.proxy else "direct connection (no proxy)")
        self.failures: list[str] = []
        # Artists the site curates (photo, type, links); the service saves them to the artist book.
        self.artist_profiles: list[ArtistProfile] = []
        # Profiles this source already saved, by casefolded name (filled by the service), so a
        # catalog that needs a request per artist only fetches the new ones.
        self.known_artists: dict[str, ArtistProfile] = {}
        # Artwork URLs already stored per source_event_id (filled by the service before a run),
        # so a source that needs an extra request per event for artwork can skip known events.
        self.known_images: dict[str, list[str]] = {}
        # Ticket links that other sources' upcoming events point to (extra.purchase_url; filled by the
        # service), so a site whose list hides some events (private ones) can still read those pages.
        self.linked_urls: list[str] = []
        # Whether an event will be kept (e.g. it is in the target city). Set by the service; a
        # source spends extra per-event requests (artwork) only on events that pass it.
        self.wanted: Callable[[ScrapedEvent], bool] = lambda event: True
        self._client: httpx.AsyncClient | None = None
        self._retired_clients: list[httpx.AsyncClient] = []  # replaced after a proxy failure; closed at exit
        self._semaphore = asyncio.Semaphore(self.http.concurrency)
        self._backoff = wait_exponential(multiplier=self.http.backoff_seconds, max=60)

    # ----------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        self._client = self._new_client(self.http.proxy)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for client in [*self._retired_clients, self._client]:
            if client is not None:
                await client.aclose()
        self._client, self._retired_clients = None, []

    def _new_client(self, proxy: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.http.timeout_seconds,
            follow_redirects=True,
            proxy=proxy,
            headers={
                "User-Agent": self.http.user_agent,
                "Accept-Language": self.http.accept_language,
                **self.default_headers(),
                **self.http.headers,
            },
        )

    def _next_proxy(self, failed: httpx.AsyncClient, exc: Exception) -> None:
        """Move to the next proxy of the pool, once per failed client (parallel requests share it)."""
        if failed is not self._client or not self._spare_proxies:
            return
        proxy = self._spare_proxies.pop(0)
        self.log.warning("proxy failed (%s): switching to %s", type(exc).__name__, proxy.rpartition("@")[2])
        self._retired_clients.append(failed)
        self._client = self._new_client(proxy)

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
    def _wait(self, state: RetryCallState) -> float:
        """Server-requested Retry-After when given, otherwise exponential backoff."""
        retry_after = _retry_after(state.outcome.exception() if state.outcome else None)
        return retry_after if retry_after is not None else self._backoff(state)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.http.max_retries + 1),
            wait=self._wait,
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:  # held per attempt, never while backing off
                    if self.http.request_delay_seconds:
                        await asyncio.sleep(self.http.request_delay_seconds)
                    client = self._client
                    try:
                        response = await client.request(method, url, **kwargs)
                    except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        self._next_proxy(client, exc)  # retried on the next proxy (a TransportError)
                        raise
                if self.is_blocked(response):
                    raise SourceBlockedError(f"{self.name}: access denied by the site ({response.status_code})")
                response.raise_for_status()
        return response

    async def get_json(self, url: str, *, expect: type | None = None, **kwargs: Any) -> Any:
        return _json(await self.request("GET", url, **kwargs), expect)

    async def post_json(self, url: str, *, expect: type | None = None, **kwargs: Any) -> Any:
        return _json(await self.request("POST", url, **kwargs), expect)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        return (await self.request("GET", url, **kwargs)).text

    # ----------------------------------------------------------------- failure log
    def record_failure(self, what: str, exc: BaseException) -> None:
        message = f"{what}: {type(exc).__name__}: {exc}"[:500]
        self.failures.append(message)
        self.log.warning("skipped %s", message)

    async def optional(self, what: str, call: Awaitable[T]) -> T | None:
        """Secondary data (tickets, details): on failure record it and return None, so the
        item is still saved with what its primary data provides."""
        try:
            return await call
        except SourceBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade instead of dropping the item
            self.record_failure(what, exc)
            return None

    # ----------------------------------------------------------------- listings
    async def paginate(
        self,
        fetch_page: Callable[[int], Awaitable[tuple[list[T], int | None]]],
        page_size: int,
        max_pages: int,
    ) -> list[T]:
        """Collect every page of a listing ("load more"), fetching pages in parallel.

        ``fetch_page(index)`` returns ``(items, total)``; ``total`` may be None. With a
        known total all remaining pages are requested at once; otherwise windows of
        ``concurrency`` pages are fetched until a short page marks the end. A failing
        page (after retries) is recorded and skipped; the first page must succeed.
        """

        async def page(index: int) -> tuple[list[T], int | None, bool]:
            try:
                batch, total = await fetch_page(index)
                return list(batch or []), to_int(total), True
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad page never discards the others
                self.record_failure(f"listing page {index}", exc)
                return [], None, False

        first, total = await fetch_page(0)
        items = list(first or [])
        total = to_int(total)
        if len(items) < page_size:
            if total is not None and total > len(items):
                self.log.warning("first page has %d of %d items: page size capped below %d?", len(items), total, page_size)
            return items

        if total is not None:
            needed = -(-total // page_size)
            if needed > max_pages:
                self.log.warning("listing truncated at max_pages=%d (%d items reported)", max_pages, total)
            for batch, _, _ in await gather_or_cancel(page(i) for i in range(1, min(needed, max_pages))):
                items.extend(batch)
            return items

        window = self.http.concurrency
        for start in range(1, max_pages, window):
            pages = await gather_or_cancel(page(i) for i in range(start, min(start + window, max_pages)))
            for batch, _, _ in pages:
                items.extend(batch)
            succeeded = [batch for batch, _, ok in pages if ok]
            if not succeeded or any(len(batch) < page_size for batch in succeeded):
                return items
        self.log.warning("listing truncated at max_pages=%d", max_pages)
        return items

    # ----------------------------------------------------------------- details
    async def stream_isolated(
        self, items: Iterable[T], fn: Callable[[T], Awaitable[R | None]], label: Callable[[T], str]
    ) -> AsyncIterator[R]:
        """Run ``fn`` over ``items`` concurrently and yield results as they complete.

        One failing item is recorded and skipped; results are yielded immediately so
        they are saved progressively. ``SourceBlockedError`` cancels the rest.
        """

        async def guarded(item: T) -> R | None:
            try:
                return await fn(item)
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate per-item failures
                try:
                    name = label(item)
                except Exception:  # noqa: BLE001 - a bad item must not break its own error report
                    name = repr(item)[:80]
                self.record_failure(name, exc)
                return None

        tasks = [asyncio.ensure_future(guarded(item)) for item in items]
        try:
            for next_done in asyncio.as_completed(tasks):
                result = await next_done
                if result is not None:
                    yield result
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _json(response: httpx.Response, expect: type | None) -> Any:
    """Decoded JSON body; a non-JSON body (HTML error page with 200) or wrong shape -> ParseError."""
    try:
        payload = response.json()
    except ValueError as exc:
        content_type = response.headers.get("content-type", "?")
        raise ParseError(f"{response.request.url}: expected JSON, got {content_type}") from exc
    if expect is not None and not isinstance(payload, expect):
        raise ParseError(f"{response.request.url}: expected {expect.__name__}, got {type(payload).__name__}")
    return payload
