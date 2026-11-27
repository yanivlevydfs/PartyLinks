"""Short event links /e/<code> (partylinks.shortlinks): codes, lookup, old-address redirects, duplicates."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from partylinks import shortlinks
from partylinks.api import create_app
from partylinks.db import EventRow, upsert_events
from partylinks.enrichment import Enricher
from partylinks.seo import event_path
from tests.test_storage_api import event, seed, settings_for

UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def test_codes_are_short_stable_unambiguous_and_distinct() -> None:
    ids = [f"source{i % 7}:{i}" for i in range(20000)]
    codes = [shortlinks.candidate(i) for i in ids]
    assert codes == [shortlinks.candidate(i) for i in ids]  # the same id, the same code, every time
    assert all(
        shortlinks.LENGTH <= len(c) <= shortlinks.LENGTH + 2 and set(c) <= set(shortlinks.ALPHABET) for c in codes
    )
    assert sum(len(c) == shortlinks.LENGTH for c in codes) > len(codes) * 0.99  # 8 characters, but for a blocked word
    assert not set("".join(codes)) & set("01ilo" + "aeuy" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ")  # nothing to misread
    assert len(set(codes)) == len(codes)
    assert shortlinks.candidate("test:a", 1) != shortlinks.candidate("test:a")  # the next one, after a clash


def test_a_link_names_its_code_whatever_a_chat_app_glued_on() -> None:
    code = shortlinks.candidate("test:a")
    assert shortlinks.normalize(code) == code
    assert shortlinks.normalize(code.upper()) == code
    assert shortlinks.normalize(f"{code}).") == code
    assert shortlinks.normalize("party-2026-11-28-goout-15f68ac1") is None
    assert shortlinks.normalize("") is None and shortlinks.normalize("ab0") is None


def test_stored_events_get_codes_and_a_clash_takes_the_next_one(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=3)
    db = seed(settings, [event("a", soon), event("b", soon)])
    with db.session() as session:
        codes = dict(session.execute(select(EventRow.id, EventRow.code)).all())
        assert codes["test:a"] == shortlinks.candidate("test:a")
        # Another event already holds c's own code: c gets its next candidate.
        session.execute(update(EventRow).where(EventRow.id == "test:b").values(code=shortlinks.candidate("test:c")))
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        upsert_events(
            session, [e for e in map(enricher.enrich, [event("c", soon), event("a", soon, title="New title")]) if e]
        )
    with db.session() as session:
        codes = dict(session.execute(select(EventRow.id, EventRow.code)).all())
    assert codes["test:c"] == shortlinks.candidate("test:c", 1)
    assert codes["test:a"] == shortlinks.candidate("test:a")  # a rescan never changes a stored code
    assert len(set(codes.values())) == 3


def test_backfill_gives_old_rows_a_code(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=3)
    db = seed(settings, [event(str(i), soon) for i in range(5)])
    with db.session() as session:
        session.execute(update(EventRow).values(code=None))
    assert shortlinks.backfill(db, batch=2, pause=0) == 5
    assert shortlinks.backfill(db, pause=0) == 0
    with db.session() as session:
        codes = dict(session.execute(select(EventRow.id, EventRow.code)).all())
    assert codes == {f"test:{i}": shortlinks.candidate(f"test:{i}") for i in range(5)}


def _canonical(html: str) -> tuple[str, str, str]:
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    og = re.search(r'<meta property="og:url" content="([^"]+)"', html)
    ld = re.search(r'"url": ?"([^"]+/e/[^"#]+)"', html)
    assert canon and og and ld, html[:2000]
    return canon.group(1), og.group(1), ld.group(1)


def test_the_short_link_is_the_one_address_of_an_event_page(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=3)
    db = seed(settings, [event("a", soon), event("dup", soon), event("past", datetime.now(UTC) - timedelta(days=3))])
    with db.session() as session:
        a = session.get(EventRow, "test:a")
        assert a is not None and a.code and a.slug
        code, slug = a.code, a.slug
        dup = session.get(EventRow, "test:dup")
        assert dup is not None and dup.code
        dup_code = dup.code
        # The same event on another site: a duplicate listing of a's group.
        session.execute(update(EventRow).where(EventRow.id == "test:dup").values(group_id="test:a", is_primary=False))
        assert event_path(a) == f"/e/{code}"
    short = f"/e/{code}"
    with TestClient(create_app(settings, start_scheduler=False), follow_redirects=False) as client:
        page = client.get(short, headers=UA)
        assert page.status_code == 200
        url = f"{settings.site.base_url}{short}"
        assert _canonical(page.text) == (url, url, url)  # canonical, og:url, JSON-LD url
        # The old long address, a code in capitals or with a tail, and a duplicate listing's code: 301 to it.
        for old in (
            f"/event/{slug}",
            "/event/test:a",
            f"/e/{code.upper()}",
            f"/e/{code}/techno-night",
            f"/e/{dup_code}",
        ):
            moved = client.get(old, headers=UA)
            assert moved.status_code == 301 and moved.headers["location"] == short, old
        assert client.get(f"/event/{slug}?lang=en", headers=UA).headers["location"] == f"{short}?lang=en"
        # Over (not purged yet), or no such code: gone.
        assert client.get("/event/test:past", headers=UA).status_code == 301
        assert client.get("/event/test:past", headers=UA, follow_redirects=True).status_code == 410
        assert client.get("/e/zzzzzzz", headers=UA).status_code == 410
        # The app's data: looked up by code, a duplicate's code gives the primary, and every card carries its code.
        assert client.get(f"/events/{code}", headers=UA).json()["id"] == "test:a"
        assert client.get(f"/events/{dup_code}", headers=UA).json()["id"] == "test:a"
        items = client.get("/events", headers=UA).json()["items"]
        assert {e["id"]: e["code"] for e in items}["test:a"] == code
        assert short in client.get("/sitemap-1.xml", headers=UA).text


def test_a_cached_page_of_a_listing_merged_since_answers_its_301(tmp_path: Path) -> None:
    """Nov 2026: Ticketmaster's "נסרין", merged into Eventer's copy, kept its page (200) while visits refreshed it."""
    settings = settings_for(tmp_path)
    settings.seo.html_ttl_seconds = 0.01
    soon = datetime.now(UTC) + timedelta(days=3)
    db = seed(settings, [event("a", soon), event("dup", soon)])
    with db.session() as session:
        code, dup_code = session.get_one(EventRow, "test:a").code, session.get_one(EventRow, "test:dup").code
    with TestClient(create_app(settings, start_scheduler=False), follow_redirects=False) as client:
        assert client.get(f"/e/{dup_code}", headers=UA).status_code == 200  # its own page, cached
        with db.session() as session:
            session.execute(
                update(EventRow).where(EventRow.id == "test:dup").values(group_id="test:a", is_primary=False)
            )
        deadline = time.monotonic() + 10
        while (moved := client.get(f"/e/{dup_code}", headers=UA)).status_code == 200 and time.monotonic() < deadline:
            time.sleep(0.05)  # the stale copy is served once while the refresh finds the merge
        assert moved.status_code == 301 and moved.headers["location"] == f"/e/{code}"


def test_an_old_address_of_a_live_event_reaches_it_and_a_gone_one_says_it_is_over(tmp_path: Path) -> None:
    """Nov 2026: makore gave its Chopin marathon a new id; the old link said "this party is over" while it was
    on sale. The same title and date under a new hash is the same event: 301. A party that is over says so."""
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=3)
    db = seed(settings, [event("a", soon)])
    with db.session() as session:
        a = session.get_one(EventRow, "test:a")
        assert a.slug and a.code
        old, code = re.sub(r"-[0-9a-f]{8}$", "-deadbeef", a.slug), a.code
        other_source = re.sub(r"-test-[0-9a-f]{8}$", "-ogo-0badf00d", a.slug)
    assert old != a.slug
    with TestClient(create_app(settings, start_scheduler=False), follow_redirects=False) as client:
        for gone_address in (old, other_source):
            moved = client.get(f"/event/{gone_address}", headers=UA)
            assert moved.status_code == 301 and moved.headers["location"] == f"/e/{code}", gone_address
        # The address's own (Israel) date, not soon's UTC one: from 00:00 to 02:00 in Israel they differ.
        over = client.get(f"/event/{re.sub(r'\d{4}-\d{2}-\d{2}(?=-test-)', '2020-01-01', old)}", headers=UA)
        assert over.status_code == 410
        assert "<title>המסיבה הזאת כבר נגמרה" in over.text and "העמוד לא נמצא" not in over.text
