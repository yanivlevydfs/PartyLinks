"""The worker's pass: MusicBrainz links for lineup artists with upcoming shows, most shows first.

Plain WS/2 JSON over httpx (already a dependency) with tenacity retries, not ``musicbrainzngs``: that
library is XML-only, last released in 2020, and keeps its own global rate limiter and retry loop.

Per name: a search (``artist?query=``), and for an accepted hit one lookup of its URL relations
(``artist/<mbid>?inc=url-rels``). A refresh of a found artist is that lookup alone. Every answer is kept
in ``artist_links`` (hits and misses); a request that failed is not an answer and is never stored.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from partylinks.artist_links.config import ArtistLinksSettings
from partylinks.artist_links.models import ArtistLinkRow, Link, clear_cache, merge, name_key
from partylinks.artistdb import ArtistRow
from partylinks.db import Database, EventRow, dialect_insert, upcoming_filter, utcnow, with_db_retry
from partylinks.scrapers.base import _is_retryable, _retry_after

log = logging.getLogger(__name__)

_HEBREW = re.compile(r"[֐-׿]")
_LUCENE = re.compile(r'(["\\])')

# One request at a time across the process, at most one per min_interval_seconds (MusicBrainz policy).
_rate_lock = threading.Lock()
_last_request = 0.0


class Unavailable(Exception):
    """A request failed after its retries: no answer (not a miss)."""


@dataclass
class PassStats:
    asked: int = 0  # MusicBrainz requests
    hits: int = 0
    misses: int = 0
    errors: int = 0
    skipped: int = 0  # names answered recently enough (the cache)
    pending: int = 0  # names still to ask when the pass ended (budget, breaker or stop)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def pick(candidates: list[dict[str, Any]], name: str, min_score: int) -> dict[str, Any] | None:
    """The one search result that is surely this lineup name, or None.

    Only results scored >= ``min_score`` whose name or an alias equals the name (name_key). Exactly one
    such result wins; with several, a Hebrew name takes the single Israeli one (country IL). Otherwise
    None: a wrong artist's links are worse than none."""
    key = name_key(name)
    exact = [
        c for c in candidates
        if int(c.get("score") or 0) >= min_score
        and key in {name_key(n) for n in [c.get("name") or "", *(a.get("name") or "" for a in c.get("aliases") or [])]}
    ]  # fmt: skip
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1 and _HEBREW.search(name):
        israeli = [
            c for c in exact if c.get("country") == "IL" or (c.get("area") or {}).get("iso-3166-1-codes") == ["IL"]
        ]
        if len(israeli) == 1:
            return israeli[0]
    return None


def url_links(artist: dict[str, Any], services: list[str]) -> list[Link]:
    """A MusicBrainz artist's current URL relations as one link per shown service."""
    urls = [
        (rel["url"]["resource"], rel.get("type") == "official homepage")
        for rel in artist.get("relations") or []
        if rel.get("target-type") == "url" and not rel.get("ended") and (rel.get("url") or {}).get("resource")
    ]
    return merge(urls, services)


class MusicBrainz:
    """Rate-limited, retried WS/2 client. ``get`` raises Unavailable when a request fails for good."""

    def __init__(self, cfg: ArtistLinksSettings, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.client = client or httpx.Client(
            base_url=cfg.url,
            timeout=cfg.timeout_seconds,
            headers={"User-Agent": cfg.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
        self._backoff = wait_exponential(multiplier=cfg.backoff_seconds, max=60)
        self.requests = 0

    def _wait(self, state: Any) -> float:
        after = _retry_after(state.outcome.exception() if state.outcome else None)
        return after if after is not None else self._backoff(state)

    def _throttle(self) -> None:
        global _last_request
        with _rate_lock:
            delay = _last_request + self.cfg.min_interval_seconds - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _last_request = time.monotonic()

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """The JSON answer; None for 404 (an MBID MusicBrainz deleted)."""

        def once() -> dict[str, Any] | None:
            self._throttle()
            self.requests += 1
            response = self.client.get(path, params={**params, "fmt": "json"})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

        try:
            for attempt in Retrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(self.cfg.attempts),
                wait=self._wait,
                reraise=True,
            ):
                with attempt:
                    return once()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: a body that is not JSON
            raise Unavailable(str(exc)) from exc
        raise Unavailable("no attempt")  # pragma: no cover (tenacity always tries once)

    def search(self, name: str) -> list[dict[str, Any]]:
        phrase = _LUCENE.sub(r"\\\1", name)  # a quote or backslash in the name, escaped inside the phrase
        answer = self.get(
            "/artist", {"query": f'artist:"{phrase}" OR alias:"{phrase}"', "limit": self.cfg.search_limit}
        )
        return (answer or {}).get("artists") or []

    def links(self, mbid: str) -> list[Link] | None:
        """None: no such artist any more (a merged MBID redirects and is followed)."""
        answer = self.get(f"/artist/{mbid}", {"inc": "url-rels"})
        return None if answer is None else url_links(answer, self.cfg.services)


def upcoming_artists(db: Database) -> list[str]:
    """Lineup names of upcoming events, most upcoming shows first (blocked names left out)."""
    with db.session(write=False) as session:
        lineups = session.scalars(select(EventRow.lineup).where(upcoming_filter(), EventRow.lineup.is_not(None))).all()
        blocked = set(session.scalars(select(ArtistRow.key).where(ArtistRow.blocked.is_(True))))
    shows: Counter[str] = Counter()
    spelling: dict[str, str] = {}
    for lineup in lineups:
        for name in {n.strip() for n in lineup or [] if n and n.strip()}:
            if name.casefold() not in blocked:
                key = name_key(name)
                spelling.setdefault(key, name)
                shows[key] += 1
    return [spelling[k] for k, _ in sorted(shows.items(), key=lambda kv: (-kv[1], kv[0]))]


def _save(db: Database, row: dict[str, Any]) -> None:
    def write() -> None:
        with db.session() as session:
            stmt = dialect_insert(session, ArtistLinkRow).values(row)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"], set_={c: stmt.excluded[c] for c in row if c != "key"}
                )
            )

    with_db_retry("artist links", write)


def run_pass(
    db: Database, cfg: ArtistLinksSettings, stop: threading.Event | None = None, client: MusicBrainz | None = None
) -> PassStats:
    """Ask MusicBrainz about names never answered, misses older than retry_failed_days and hits older than
    refresh_days, at most ``max_lookups_per_run`` requests; stops after ``max_consecutive_failures``."""
    stats = PassStats()
    if not cfg.enabled:
        return stats
    names = [n for n in upcoming_artists(db) if len(name_key(n)) >= cfg.min_name_length]
    with db.session(write=False) as session:
        cache = {r.key: r for r in session.scalars(select(ArtistLinkRow))}
        session.expunge_all()
    now = utcnow()
    retry_before, refresh_before = now - timedelta(days=cfg.retry_failed_days), now - timedelta(days=cfg.refresh_days)
    todo = []
    for name in names:  # one spelling per name_key
        row = cache.get(name_key(name))
        if row is None or row.looked_up_at < (refresh_before if row.found else retry_before):
            todo.append((name, row))
        else:
            stats.skipped += 1
    mb = client or MusicBrainz(cfg)
    failures = 0
    try:
        for index, (name, row) in enumerate(todo):
            # A search may need a second request for the hit's links: start a name only with room for both.
            need = 1 if row is not None and row.found and row.mbid else 2
            if (
                (stop is not None and stop.is_set())
                or mb.requests + need > cfg.max_lookups_per_run
                or failures >= cfg.max_consecutive_failures
            ):
                stats.pending = len(todo) - index
                break
            try:
                mbid = row.mbid if need == 1 and row is not None else None
                if mbid is None and (hit := pick(mb.search(name), name, cfg.min_score)):
                    mbid = hit["id"]
                links = mb.links(mbid) if mbid else None
                if links is None:  # not found, or its MBID is gone: a miss (searched again after retry_failed_days)
                    mbid = None
            except Unavailable as exc:
                failures += 1
                stats.errors += 1
                log.warning("artist links: MusicBrainz request for %r failed: %s", name, exc)
                continue
            failures = 0
            stats.hits += mbid is not None
            stats.misses += mbid is None
            _save(db, {"key": name_key(name), "name": name, "mbid": mbid, "links": links or None,
                       "found": mbid is not None, "looked_up_at": utcnow()})  # fmt: skip
    finally:
        stats.asked = mb.requests
        if client is None:
            mb.client.close()
    if stats.hits or stats.misses:
        clear_cache()
    log.info(
        "artist links: %d requests, %d found, %d not found, %d errors, %d cached, %d left for later",
        stats.asked,
        stats.hits,
        stats.misses,
        stats.errors,
        stats.skipped,
        stats.pending,
    )
    return stats


def counts(db: Database) -> dict[str, int]:
    """For the admin: upcoming lineup names found / not found / not asked yet."""
    with db.session(write=False) as session:
        answered = {k: f for k, f in session.execute(select(ArtistLinkRow.key, ArtistLinkRow.found))}
    keys = {name_key(n) for n in upcoming_artists(db)}
    found = sum(1 for k in keys if answered.get(k) is True)
    return {
        "upcoming_artists": len(keys),
        "found": found,
        "not_found": sum(1 for k in keys if answered.get(k) is False),
        "not_asked": sum(1 for k in keys if k not in answered),
    }
