"""Artists' links from MusicBrainz: conservative matching, the pass's cache windows / budget / retries /
breaker, and the links on the event API, the artist page and the JSON-LD."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from partylinks.api import create_app
from partylinks.artist_links import models
from partylinks.artist_links.config import ArtistLinksSettings
from partylinks.artist_links.lookup import MusicBrainz, pick, run_pass
from partylinks.artist_links.models import ArtistLinkRow, name_key
from partylinks.artistdb import ArtistBook
from partylinks.artists import ArtistProfile
from partylinks.db import Database, EventRow
from tests.test_storage_api import event, seed, settings_for

MBID = "11111111-1111-1111-1111-111111111111"
RELATIONS = {
    "relations": [
        {"target-type": "url", "type": "free streaming", "url": {"resource": "https://open.spotify.com/artist/abc"}},
        {"target-type": "url", "type": "social network", "url": {"resource": "https://www.instagram.com/infected"}},
        {"target-type": "url", "type": "youtube", "url": {"resource": "https://www.youtube.com/@infected"}},
        {"target-type": "url", "type": "official homepage", "url": {"resource": "https://infected-mushroom.com"}},
        {"target-type": "url", "type": "other databases", "url": {"resource": "https://www.discogs.com/artist/1"}},
        {"target-type": "url", "type": "social network", "ended": True, "url": {"resource": "https://twitter.com/old"}},
    ]
}


def cand(name: str, score: int = 100, mbid: str = MBID, **extra: Any) -> dict[str, Any]:
    return {"id": mbid, "name": name, "score": score, **extra}


# --------------------------------------------------------------------------- matching
def test_name_key_ignores_case_accents_niqqud_and_punctuation() -> None:
    assert name_key("Dalí!") == name_key("DALI") == "dali"
    assert name_key("שָׁלוֹם") == name_key("שלום")
    assert name_key("A-Trak") == name_key("a trak")


def test_pick_accepts_only_one_exact_candidate() -> None:
    assert pick([cand("Infected Mushroom"), cand("Infected", mbid="x")], "INFECTED MUSHROOM", 90)["id"] == MBID
    assert pick([cand("Someone", aliases=[{"name": "Skazi"}])], "skazi", 90) is not None  # an alias counts
    assert pick([cand("Adam Ten")], "Adam", 90) is None  # not the same name
    assert pick([cand("Adam", score=80)], "Adam", 90) is None  # score too low
    assert pick([cand("Adam", mbid="a"), cand("Adam", mbid="b")], "Adam", 90) is None  # ambiguous
    # Ambiguous, but a Hebrew name and only one of them is Israeli.
    two = [cand("נועה", mbid="il", country="IL"), cand("נועה", mbid="us", country="US")]
    assert pick(two, "נועה", 90)["id"] == "il"
    assert (
        pick([cand("Noa", mbid="il", country="IL"), cand("Noa", mbid="us")], "Noa", 90) is None
    )  # Latin name: no guess


# --------------------------------------------------------------------------- the pass
class FakeMB:
    """MusicBrainz over httpx.MockTransport. ``fail``: statuses answered first, in order."""

    def __init__(self, fail: list[int] | None = None, retry_after: str | None = None) -> None:
        self.paths: list[str] = []
        self.fail = list(fail or [])
        self.retry_after = retry_after

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        assert request.headers["user-agent"].startswith("SPOTX/")
        if self.fail:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(self.fail.pop(0), headers=headers)
        if request.url.path.endswith("/artist"):
            query = request.url.params["query"]
            return httpx.Response(200, json={"artists": [cand("Infected Mushroom")] if "Infected" in query else []})
        return httpx.Response(200, json=RELATIONS)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No real waiting: the rate limit's and the retries' sleeps are recorded."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


def _db(tmp_path: Path, lineups: dict[str, list[str]]) -> Database:
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings_for(tmp_path), [event(i, future) for i in lineups])
    with db.session() as session:
        for row in session.scalars(select(EventRow)):
            row.lineup = lineups[row.source_event_id]
    return db


def _run(db: Database, fake: FakeMB, **cfg: Any) -> Any:
    settings = ArtistLinksSettings(**({"backoff_seconds": 0} | cfg))
    client = MusicBrainz(
        settings,
        httpx.Client(
            base_url=settings.url, transport=httpx.MockTransport(fake), headers={"User-Agent": settings.user_agent}
        ),
    )
    return run_pass(db, settings, client=client)


def _rows(db: Database) -> dict[str, ArtistLinkRow]:
    with db.session(write=False) as session:
        rows = {r.key: r for r in session.scalars(select(ArtistLinkRow))}
        session.expunge_all()
    return rows


def test_pass_budget_cache_windows_and_mbid_refresh(tmp_path: Path, sleeps: list[float]) -> None:
    db = _db(tmp_path, {"a": ["Infected Mushroom", "Nobody Known"], "b": ["Infected Mushroom"], "c": ["DJ"]})
    fake = FakeMB()
    stats = _run(db, fake, max_lookups_per_run=3)  # the most-booked name first: search + links = 2, no room for another
    assert fake.paths == ["/ws/2/artist", f"/ws/2/artist/{MBID}"]
    assert (stats.asked, stats.hits, stats.pending) == (2, 1, 1)  # "DJ" is too short to ask
    row = _rows(db)["infected mushroom"]
    assert row.found and row.mbid == MBID
    assert [link["service"] for link in row.links] == [
        "spotify",
        "youtube",
        "instagram",
        "website",
    ]  # no discogs, no ended

    fake.paths.clear()
    stats = _run(db, fake)
    assert fake.paths == ["/ws/2/artist"] and stats.misses == 1 and stats.skipped == 1  # the miss is stored
    assert not _rows(db)["nobody known"].found

    fake.paths.clear()
    assert _run(db, fake).asked == 0 and fake.paths == []  # everything answered within its window

    old = datetime.now(UTC) - timedelta(days=91)
    with db.session() as session:
        session.execute(update(ArtistLinkRow).values(looked_up_at=old))
    _run(db, fake)  # a hit is re-read by its MBID (no search); a miss older than 14 days is searched again
    assert sorted(fake.paths) == sorted([f"/ws/2/artist/{MBID}", "/ws/2/artist"])


def test_retry_after_is_honoured(tmp_path: Path, sleeps: list[float]) -> None:
    db = _db(tmp_path, {"a": ["Infected Mushroom"]})
    fake = FakeMB(fail=[503], retry_after="7")
    stats = _run(db, fake)
    assert 7 in sleeps and stats.hits == 1 and stats.errors == 0
    assert fake.paths[:2] == ["/ws/2/artist", "/ws/2/artist"]


def test_breaker_stops_the_pass_and_failures_are_not_misses(tmp_path: Path, sleeps: list[float]) -> None:
    db = _db(tmp_path, {"a": ["Alpha One"], "b": ["Bravo Two"], "c": ["Charlie Three"]})
    fake = FakeMB(fail=[503] * 100)
    stats = _run(db, fake, attempts=2, max_consecutive_failures=2)
    assert len(fake.paths) == 4  # 2 names x 2 tries, then the pass stops
    assert (stats.errors, stats.pending) == (2, 1)
    assert _rows(db) == {}  # nothing stored: asked again next pass


# --------------------------------------------------------------------------- the site
def test_links_on_the_event_api_artist_page_and_json_ld(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    future = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("show", future)])
    with db.session() as session:
        session.scalars(select(EventRow)).one().lineup = ["Infected Mushroom", "Nobody"]
        session.add(ArtistLinkRow(key="infected mushroom", name="Infected Mushroom", mbid=MBID, found=True, links=[
            {"service": "instagram", "url": "https://www.instagram.com/infected"},
            {"service": "spotify", "url": "https://open.spotify.com/artist/mb"},
        ]))  # fmt: skip
    # A ticket site's curated profile wins for its service (Spotify), MusicBrainz adds the rest.
    ArtistBook(db).save_profiles(
        [ArtistProfile(name="INFECTED MUSHROOM", source="goout", spotify_url="https://open.spotify.com/artist/curated")]
    )
    models.clear_cache()
    expected = [
        {"service": "spotify", "url": "https://open.spotify.com/artist/curated"},
        {"service": "instagram", "url": "https://www.instagram.com/infected"},
    ]
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        detail = client.get("/events/test:show").json()
        assert detail["lineup_links"] == {"Infected Mushroom": expected}
        info = client.get("/landing-info", params={"path": "/artist/Infected%20Mushroom"}).json()
        assert info["artist_links"] == expected
    from partylinks.seo import artist_page, event_page

    urls = [link["url"] for link in expected]
    with db.session() as session:  # the server-rendered pages' JSON-LD
        performers = event_page(session, settings, "he", "test:show").jsonld[0]["performer"]  # type: ignore[union-attr]
        assert performers[0]["sameAs"] == urls and "sameAs" not in performers[1]
        assert artist_page(session, settings, "en", "Infected Mushroom").jsonld[0]["sameAs"] == urls
    models.clear_cache()
