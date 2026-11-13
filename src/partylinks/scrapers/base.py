"""Scraper contract and per-source transport.

Every source owns one subclass of :class:`BaseScraper`, its own settings model and
its own HTTP client instance. Nothing here knows about any specific site; the
only things shared are the retry/rate-limit transport, the resilient listing /
detail helpers and the output model.

Resilience rules implemented here (so every source gets them):
* transient HTTP errors are retried with backoff, honouring ``Retry-After``;
  a concurrency slot is held only while a request is in flight, not while waiting;
* a body that is not the JSON shape a caller expects raises ``ParseError``;
* a listing page or detail item that came back without its data (``ParseError``) is tried once more;
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
import random
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from http import HTTPStatus
from typing import Any, ClassVar, Self, TypeVar, cast

import httpx
from curl_cffi import requests as curl_requests
from pydantic import BaseModel, ConfigDict, Field, field_validator
from socksio.exceptions import SOCKSError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from partylinks.artists import ArtistProfile
from partylinks.config import HttpSettings, get_settings, validate_cron
from partylinks.logs import first_line
from partylinks.models import ScrapedEvent
from partylinks.pace import Pace
from partylinks.parsing import ParseError, cf_emails, to_int

T = TypeVar("T")
R = TypeVar("R")

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
THROTTLED = (429, 503)  # a proxied source moves to the next proxy on these


def _firewall_code(status: int) -> bool:
    """A status no HTTP spec defines (haifa.muni.il 482, tel-aviv.gov.il 472 / 571): a firewall refusing the IP."""
    try:
        HTTPStatus(status)
    except ValueError:
        return status >= 400
    return False


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
    # The site only answers Israeli IPs: try `http.israel_proxy` first (see HttpSettings).
    israel_proxy: bool = False
    # Go out only through our Israeli VPN (http.israel_vpn, Railway production): no free proxies, no pool.
    vpn: bool = False
    # A seasonal / small site with nothing listed now: a run with 0 events is healthy (no retries, no pause).
    may_be_empty: bool = False

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        return validate_cron(value)


class SourceBlockedError(RuntimeError):
    """The site refused automated access (e.g. a bot-protection checkpoint).

    Raised once the proxy pool is used up: a refusal first moves the source to the next proxy
    of ``source_proxy`` (the site may refuse one IP, not the others). The run then stops and is
    recorded as ``blocked``.
    """


class _RefusedByProxyIP(httpx.TransportError):
    """The site refused this proxy's IP; retried on the next proxy of the pool."""


class _Throttled(httpx.TransportError):
    """Throttled on our own IP at the slowest pace; retried on the reserve proxy."""


def _proxy_500(exc: Exception | None) -> bool:
    """The proxy itself answered 500: its exit could not reach the site (evomi), not a dead proxy."""
    return isinstance(exc, httpx.ProxyError) and "500" in str(exc)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def _retry_after(exc: BaseException | None, cap: float | None = None) -> float | None:
    """The Retry-After header's seconds, at most ``cap`` (default: http.max_retry_after_seconds)."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    seconds = to_int(exc.response.headers.get("Retry-After"))
    cap = get_settings().http.max_retry_after_seconds if cap is None else cap
    return min(float(seconds), cap) if seconds is not None and seconds >= 0 else None


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


_feed_cache: dict[str, tuple[float, list[str]]] = {}
# The Israeli proxy that last worked, shared by every `israel_proxy` source (sources run on threads
# of one process): each starts on it and keeps it until it fails to connect, then the next one
# that works becomes the shared one. ponytail: in memory, a restart walks the pool again.
_israel_good: str | None = None
_israel_lock = threading.Lock()  # check-and-set across source threads
# Israeli proxies a source just found dead (monotonic time): the others try them last, not first.
_israel_dead: dict[str, float] = {}


def _israeli_feed(url: str | None, http: HttpSettings) -> list[str]:
    """Proxy URLs from a geonode-style JSON list (``data: [{ip, port, protocols}]``), cached per URL.
    A failed fetch yields nothing: the configured list and `source_proxy` still follow."""
    if not url:
        return []
    cached = _feed_cache.get(url)
    if cached and time.monotonic() - cached[0] < http.israel_proxy_feed_ttl_seconds:
        return cached[1]
    # ponytail: sync fetch in a sync constructor blocks the loop up to 10 s every 30 min; prefetch async if that bites
    try:
        rows = httpx.get(url, timeout=http.israel_proxy_feed_timeout_seconds).raise_for_status().json()["data"]
        # httpx speaks to HTTP(S) and SOCKS5 proxies (socksio); SOCKS4 is skipped.
        scheme = lambda protocols: "http" if {"http", "https"} & set(protocols) else "socks5" if "socks5" in protocols else None
        proxies = [f"{scheme(r.get('protocols', []))}://{r['ip']}:{r['port']}" for r in rows if scheme(r.get("protocols", []))]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logging.getLogger(__name__).warning("israel_proxy_feed failed: %s", exc)
        proxies = []
    _feed_cache[url] = (time.monotonic(), proxies)
    return proxies


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
        # A proxied source starts on the first URL of `source_proxy` and moves to the next when the
        # proxy fails or the site refuses its IP (403 / is_blocked). A per-source proxy wins.
        pool = [p.strip() for p in (http.source_proxy or "").split(",") if p.strip()] if settings.proxied else []
        # The source's name as the proxy user, so the vpn logs say who asked (@source:onecity).
        vpn = (http.israel_vpn if "@" in http.israel_vpn else http.israel_vpn.replace("://", f"://{self.name}@", 1)) if http.israel_vpn else None
        self._fallback_vpn: str | None = None
        if settings.vpn and vpn:  # our own exit and nothing else: no free proxies, no pool
            pool = [vpn]
            # One exit: no probe, and no part in the free proxies' shared "good" one (each source's URL differs).
            self._israeli = set()
        elif settings.israel_proxy:
            israeli = [p.strip() for p in (http.israel_proxy or "").split(",") if p.strip()]
            israeli = list(dict.fromkeys(_israeli_feed(http.israel_proxy_feed, http) + israeli))
            random.shuffle(israeli)  # no known good one yet: walk the free proxies in random order
            now, dead_for = time.monotonic(), http.israel_proxy_dead_seconds
            # The shared working one first, recently dead ones last (stable: the shuffle stays among equals).
            israeli.sort(key=lambda p: (p != _israel_good, now - _israel_dead.get(p, -dead_for) < dead_for))
            pool = israeli + pool
            self._israeli = set(israeli)
        else:
            self._israeli = set()
        self._vpn = settings.vpn and bool(http.israel_vpn)
        if pool and vpn and not self._vpn:
            # Every proxied source ends its pool on our VPN: when the proxies fail (evomi's "500 Internal
            # Server Error": its exit cannot reach the site), the run goes on through our Israeli exit.
            pool.append(vpn)
            self._fallback_vpn = vpn
        self._spare_proxies = pool[1:] if pool and "proxy" not in overrides else []
        # Direct by choice (proxied: false), with the proxy kept in reserve for when the site rations our IP.
        self._reserve = [] if pool or "proxy" in overrides else [p.strip() for p in (http.source_proxy or "").split(",") if p.strip()]
        self._proxy = overrides.get("proxy") or (pool[0] if pool else None)  # the client's current proxy
        self._pool_size = 1 + len(self._spare_proxies)
        if pool:
            overrides.setdefault("proxy", pool[0])
        self.http = HttpSettings.model_validate(http.model_dump() | overrides)
        self.timezone = timezone
        self.log = logging.getLogger(f"partylinks.scrapers.{self.name}")
        # Credentials stay out of the log: only the proxy's host.
        self.log.debug("via %s", self.http.proxy.rpartition("@")[2] if self.http.proxy else "direct connection (no proxy)")
        self.failures: list[str] = []
        # Artists the site curates (photo, type, links); the service saves them to the artist book.
        self.artist_profiles: list[ArtistProfile] = []
        # Profiles this source already saved, by casefolded name (filled by the service), so a
        # catalog that needs a request per artist only fetches the new ones.
        self.known_artists: dict[str, ArtistProfile] = {}
        # Venues the site curates (logo, about; venue_profiles); the service saves them.
        self.venue_profiles: list[Any] = []
        # Artwork URLs already stored per source_event_id (filled by the service before a run),
        # so a source that needs an extra request per event for artwork can skip known events.
        self.known_images: dict[str, list[str]] = {}
        # Ticket links that other sources' upcoming events point to (extra.purchase_url; filled by the
        # service), so a site whose list hides some events (private ones) can still read those pages.
        self.linked_urls: list[str] = []
        # Whether an event will be kept (e.g. it is in the target city). Set by the service; a
        # source spends extra per-event requests (artwork) only on events that pass it.
        self.wanted: Callable[[ScrapedEvent], bool] = lambda event: True
        # Called on every answer from the site (set by the service): a run still getting answers is not
        # stuck, though a slowed-down one may read for minutes without yielding an event (pace.py).
        self.progress: Callable[[], None] = lambda: None
        self._client: httpx.AsyncClient | None = None
        self._retired_clients: list[httpx.AsyncClient] = []  # replaced after a proxy failure; closed at exit
        self._pace = Pace(self.http.concurrency, self.http.request_delay_seconds, self.http.pace, self.log)
        self._probe: asyncio.Task[None] | None = None  # the Israeli pool's one parallel check (see _probe_israeli)
        self._backoff = wait_exponential(multiplier=self.http.backoff_seconds, max=self.http.backoff_max_seconds)

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
        impersonate, connect = self.http.impersonate, min(self.http.connect_timeout_seconds, self.http.timeout_seconds)
        return httpx.AsyncClient(
            # A dead proxy fails on connect: give up on it in 10 s instead of the full timeout, so a
            # pool of free proxies is walked in minutes. Reads keep the full timeout.
            timeout=httpx.Timeout(self.http.timeout_seconds, connect=connect),
            follow_redirects=True,
            event_hooks={"response": [_one_location]},
            proxy=None if impersonate else proxy,
            transport=_ImpersonatingTransport(impersonate, proxy, (connect, self.http.timeout_seconds)) if impersonate else None,
            headers={
                **({} if impersonate else {"User-Agent": self.http.user_agent}),
                "Accept-Language": self.http.accept_language,
                **self.default_headers(),
                **self.http.headers,
            },
        )

    def _next_proxy(self, failed: httpx.AsyncClient, exc: Exception | None) -> bool:
        """Move to the next proxy of the pool, once per failed client (parallel requests share it).
        True when the request should go again: on a new proxy, or one a parallel request just switched to."""
        if failed is not self._client:
            return True
        global _israel_good
        with _israel_lock:
            if exc is not None and self._proxy is not None and self._proxy in self._israeli:
                _israel_dead[self._proxy] = time.monotonic()  # fails to connect: the others skip it
                if self._proxy == _israel_good:
                    _israel_good = None  # dead: the next Israeli proxy that works takes over
            good, now, dead = _israel_good, time.monotonic(), dict(_israel_dead)
        if not self._spare_proxies:
            return False
        # Another source already found a working one: go straight to it; else the first not found dead lately.
        dead_for = self.http.israel_proxy_dead_seconds
        alive = [p for p in self._spare_proxies if now - dead.get(p, -dead_for) >= dead_for]
        proxy = good if good in self._spare_proxies else (alive or self._spare_proxies)[0]
        if self._fallback_vpn in self._spare_proxies and _proxy_500(exc):
            proxy = self._fallback_vpn  # the next exit of the same provider meets the same wall: straight to the VPN
        self._spare_proxies.remove(proxy)
        why = f"{type(exc).__name__}: {first_line(exc)[:160]}" if exc else "refused by the site"
        self.log.warning("proxy failed (%s): switching to %s", why, proxy.rpartition("@")[2])
        self._retired_clients.append(failed)
        self._client, self._proxy = self._new_client(proxy), proxy
        return True

    def _to_reserve(self, failed: httpx.AsyncClient) -> None:
        """Leave our own IP for the reserve proxy, at the configured pace again (a new IP, a new ration)."""
        if failed is not self._client:
            return  # a parallel request already moved
        proxy, self._spare_proxies, self._reserve = self._reserve[0], self._reserve[1:], []
        self.log.warning("throttled at the slowest pace on our own IP: switching to %s", proxy.rpartition("@")[2])
        self._retired_clients.append(failed)
        self._client, self._proxy = self._new_client(proxy), proxy
        self._pace = Pace(self.http.concurrency, self.http.request_delay_seconds, self.http.pace, self.log)

    @property
    def working_http(self) -> HttpSettings:
        """``http`` on the proxy the run ended on. ``http.proxy`` stays the pool's first entry, which for an
        Israeli pool is a random free proxy, mostly dead: artwork downloaded through it all failed."""
        return self.http.model_copy(update={"proxy": self._proxy or self.http.proxy})

    def is_blocked(self, response: httpx.Response) -> bool:
        """Sources override this to recognise their site's access-denied responses."""
        return False

    def is_gone(self, response: httpx.Response) -> bool:
        """Sources override this for a page the site deleted but answers with a refusal (Eventer: 403):
        not a block, so no move to the next proxy; the request fails once, as a 404 would."""
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
        retry_after = _retry_after(state.outcome.exception() if state.outcome else None, self.http.max_retry_after_seconds)
        return retry_after if retry_after is not None else self._backoff(state)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._request(method, url, **kwargs)
        except _RefusedByProxyIP as exc:
            raise SourceBlockedError(f"{self.name}: access denied by the site on every proxy ({exc})") from exc

    async def _probe_israeli(self, url: str) -> None:
        """Try every Israeli proxy of the pool at once on the source's first URL, and keep only those that
        answer (any status: a refusal is the site's, not the proxy's). Walked one by one, a pool of dead
        free proxies took the whole 30-min run; in parallel it takes ``http.israel_proxy_probe_seconds``. None answers: the
        source goes on with the rest of its pool, or stops. The whole list each time, the ones found dead
        lately too: free proxies come back, and a parallel check costs the same for 1 or 100."""
        global _israel_good
        current = [p for p in [self._proxy, *self._spare_proxies] if p]
        candidates = [p for p in current if p in self._israeli]

        # Over HTTPS, the certificate checked (the clients' default): a free proxy that intercepts TLS
        # fails here, not later on every request with CERTIFICATE_VERIFY_FAILED.
        https = str(httpx.URL(url).copy_with(scheme="https"))

        async def answers(proxy: str) -> bool:
            client = self._new_client(proxy)
            try:
                async with asyncio.timeout(self.http.israel_proxy_probe_seconds), client.stream("GET", https):
                    return True  # the headers came back: enough, a big page is not read through a slow proxy
            except Exception:  # noqa: BLE001 - whatever breaks, this proxy is no use
                return False
            finally:
                await client.aclose()

        alive = {p for p, ok in zip(candidates, await asyncio.gather(*map(answers, candidates)), strict=True) if ok}
        with _israel_lock:  # nothing marked dead here: one slow answer is no reason to skip a proxy later
            if _israel_good not in alive:
                _israel_good = None
        rest = [p for p in current if p not in self._israeli]
        pool = sorted(alive, key=lambda p: p != _israel_good) + rest
        self.log.debug("Israeli proxies: %d of %d answer", len(alive), len(self._israeli))
        if not pool:
            raise SourceBlockedError(f"{self.name}: no Israeli proxy answers ({len(self._israeli)} tried)")
        if pool[0] != self._proxy:
            if self._client is not None:
                self._retired_clients.append(self._client)
            self._client, self._proxy = self._new_client(pool[0]), pool[0]
        self._spare_proxies = pool[1:]

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        global _israel_good
        if self._israeli and self._client is not None:
            # On the shared working proxy (another source found it): no check. None yet, or it died: find
            # the next one that works, across the whole list at once.
            if self._probe is None and (_israel_good is None or self._proxy != _israel_good):
                self._probe = asyncio.ensure_future(self._probe_israeli(url))
            if self._probe is not None:
                await asyncio.shield(self._probe)  # parallel requests wait for the one check
        # The usual retries, plus one for each move to another proxy of the pool: a site that refuses
        # one proxy IP gets the next one, but a plain timeout is not retried pool-size times over.
        switches = [0]

        def stop(state: RetryCallState) -> bool:
            return state.attempt_number >= self.http.max_retries + 1 + switches[0]

        async for attempt in AsyncRetrying(
            stop=stop,
            wait=self._wait,
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                async with self._pace:  # a slot and the pause, per attempt, never while backing off
                    client = self._client
                    if client is None:
                        raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
                    try:
                        response = await client.request(method, url, **kwargs)
                    except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        switches[0] += self._next_proxy(client, exc)  # retried on the next proxy (a TransportError)
                        raise
                    except (httpx.RemoteProtocolError, httpx.ReadError) as exc:  # cut off mid-answer: a free proxy, mostly
                        if self._proxy is not None:
                            switches[0] += self._next_proxy(client, exc)
                        raise
                    except SOCKSError as exc:  # a broken SOCKS proxy ("Malformed reply"): httpx lets it through raw
                        switches[0] += self._next_proxy(client, exc)
                        raise httpx.ProxyError(f"SOCKS: {exc}") from exc
                self.progress()
                if response.status_code in THROTTLED:
                    self._pace.throttled()
                    if self._reserve and self._pace.slowest and self.http.pace.proxy_when_slowest:
                        self._to_reserve(client)
                        switches[0] += 1
                        raise _Throttled(f"{response.status_code} on our own IP at the slowest pace")
                else:
                    self._pace.answered()
                # Refused (403) or throttled (429 / 503: the site rations this IP): the next proxy, while
                # the pool lasts; then the usual backoff retries on the last one.
                refused = response.status_code in (403, *THROTTLED) or _firewall_code(response.status_code)
                refused = refused and not self.is_gone(response)
                if (self.is_blocked(response) or refused) and self._next_proxy(client, None):
                    switches[0] += 1
                    raise _RefusedByProxyIP(f"{response.status_code} via this proxy")
                if self.is_blocked(response):
                    raise SourceBlockedError(f"{self.name}: access denied by the site ({response.status_code})")
                if self._vpn and refused and response.status_code not in THROTTLED:
                    raise SourceBlockedError(f"{self.name}: the site refuses our Israeli VPN ({response.status_code})")
                # An Israeli-only site refusing the fallback proxy: the cause is the dead Israeli pool, not the site.
                if refused and self._israeli and self._proxy not in self._israeli:
                    raise SourceBlockedError(f"{self.name}: no Israeli proxy answers; the site refuses the other proxies ({response.status_code})")
                response.raise_for_status()
                if self._proxy is not None and self._proxy in self._israeli and _israel_good != self._proxy:
                    with _israel_lock:
                        _israel_good = self._proxy  # works: every Israeli-proxy source uses it from now on
                        _israel_dead.pop(self._proxy, None)
                # Every source alike: Cloudflare's "[email protected]" becomes the real address. Read once,
                # before any scraper reads .text / .json(), so set on the body httpx decodes them from.
                if (body := cf_emails(response.content)) is not response.content:
                    response._content = body
                return response
        raise AssertionError("unreachable: the last attempt returns or raises (reraise=True)")

    async def get_json(self, url: str, *, expect: Any = None, **kwargs: Any) -> Any:
        return _json(await self.request("GET", url, **kwargs), expect)

    async def post_json(self, url: str, *, expect: type | None = None, **kwargs: Any) -> Any:
        return _json(await self.request("POST", url, **kwargs), expect)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        return (await self.request("GET", url, **kwargs)).text

    async def again_on_parse_error(self, call: Callable[[], Awaitable[T]]) -> T:
        """``call()``, and once more after the backoff when its page came back without the data
        (ParseError): a site answering 200 with a half-built page mid-publish or mid-deploy.
        HTTP errors are already retried by ``request``."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.http.parse_error_attempts), wait=self._backoff, retry=retry_if_exception_type(ParseError), reraise=True
        ):
            with attempt:
                return await call()
        raise AssertionError("unreachable: the last attempt returns or raises (reraise=True)")

    # ----------------------------------------------------------------- failure log
    def record_failure(self, what: str, exc: BaseException) -> None:
        """Record one skipped item. Not while shutting down: a deploy closes the thread pools and every
        item left fails alike ("cannot schedule new futures after shutdown", 120 lines): the run stops."""
        if isinstance(exc, RuntimeError) and "cannot schedule new futures" in str(exc):
            raise exc
        message = f"{what}: {type(exc).__name__}: {first_line(exc)}"[:500]
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
        fetch_page: Callable[[int], Awaitable[tuple[list[T], int | str | None]]],  # total as given (to_int)
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
                batch, total = await self.again_on_parse_error(lambda: fetch_page(index))
                return list(batch or []), to_int(total), True
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad page never discards the others
                self.record_failure(f"listing page {index}", exc)
                return [], None, False

        first, total = await fetch_page(0)
        items = list(first or [])
        total = to_int(total)
        # A short first page ends the listing, unless the source says there are more: viagogo hides
        # some events from each page of 20 (19, 18, 17...) and still has 114 over six pages.
        if len(items) < page_size and (total is None or total <= len(items)):
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

        An item whose page lacked its data is tried once more; one still failing is recorded and
        skipped. Results are yielded immediately so they are saved progressively. ``SourceBlockedError`` cancels the rest.
        """

        async def guarded(item: T) -> R | None:
            try:
                return await self.again_on_parse_error(lambda: fn(item))
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


class _ImpersonatingTransport(httpx.AsyncBaseTransport):
    """httpx transport that sends through curl_cffi as a real browser (TLS fingerprint, HTTP/2,
    header order), so retries, proxy failover and parsing stay the same for every source."""

    # curl_cffi sets the browser's own; the body arrives already decompressed.
    _DROP_REQUEST = frozenset({"user-agent", "accept-encoding", "connection", "host", "content-length"})
    _DROP_RESPONSE = frozenset({"content-encoding", "content-length", "transfer-encoding"})

    def __init__(self, impersonate: str, proxy: str | None, timeout: tuple[float, float]) -> None:
        self._session = curl_requests.AsyncSession(
            impersonate=cast(curl_requests.BrowserTypeLiteral, impersonate), proxy=proxy, timeout=timeout
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in self._DROP_REQUEST}
        try:
            r = await self._session.request(
                cast(curl_requests.session.HttpMethod, request.method), str(request.url),
                headers=headers, data=await request.aread() or None, allow_redirects=False,
            )
        except curl_requests.RequestsError as exc:
            # Connect/timeout/proxy failures alike: a ConnectError is retried and moves to the next proxy.
            raise httpx.ConnectError(str(exc), request=request) from exc
        kept = [(k, v) for k, v in r.headers.multi_items() if v is not None and k.lower() not in self._DROP_RESPONSE]
        return httpx.Response(r.status_code, headers=kept, content=r.content, request=request)

    async def aclose(self) -> None:
        await self._session.close()


async def _one_location(response: httpx.Response) -> None:
    """A redirect sent with its Location twice (OGO's uncached 308s): httpx would join them into
    "a, a" and follow that to a 404. Keep the first."""
    if len(locations := response.headers.get_list("location")) > 1:
        response.headers["location"] = locations[0]


def _json(response: httpx.Response, expect: Any) -> Any:
    """Decoded JSON body; a non-JSON body (HTML error page with 200) or wrong shape -> ParseError."""
    try:
        payload = response.json()
    except ValueError as exc:
        content_type = response.headers.get("content-type", "?")
        raise ParseError(f"{response.request.url}: expected JSON, got {content_type}") from exc
    if expect is not None and not isinstance(payload, expect):
        wanted = getattr(expect, "__name__", str(expect))  # "dict | str" has no __name__
        raise ParseError(f"{response.request.url}: expected {wanted}, got {type(payload).__name__}")
    return payload
