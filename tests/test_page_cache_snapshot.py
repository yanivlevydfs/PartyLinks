"""The page caches survive a deploy: saved on shutdown, read back at startup as stale copies."""

from __future__ import annotations

from pathlib import Path

from partylinks.api import ResponseCache


def _filled(*pages: tuple[str, bytes]) -> ResponseCache:
    cache = ResponseCache(ttl=60, max_stale=3600)
    for path, body in pages:
        cache.get((path, ""), lambda body=body: body)
    return cache


def test_saved_pages_are_served_stale_after_a_restart_and_rebuilt_behind(tmp_path: Path) -> None:
    file = tmp_path / "pages.gz"
    assert _filled(("/", b"home"), ("/events", b"list")).save(file, 1 << 20) == 2

    fresh = ResponseCache(ttl=60, max_stale=3600)
    assert fresh.load(file) == 2
    assert fresh.peek(("/", "")) is None  # no builder yet: the route runs, hands one over and gets the copy
    built: list[str] = []

    def rebuild() -> bytes:
        built.append("/")
        return b"home v2"

    assert fresh.get(("/", ""), rebuild) == (b"home", 200)  # at once, not after a build
    fresh._pool.shutdown(wait=True)
    assert built == ["/"] and fresh.peek(("/", "")) == (b"home v2", 200)


def test_only_the_most_recently_used_fit_the_snapshot(tmp_path: Path) -> None:
    file = tmp_path / "pages.gz"
    cache = _filled(("/old", b"a" * 10), ("/new", b"b" * 10))
    assert cache.save(file, 15) == 1
    fresh = ResponseCache()
    fresh.load(file)
    assert list(fresh._items) == [("/new", "")]


def test_a_cut_off_snapshot_keeps_what_came_before(tmp_path: Path) -> None:
    import gzip

    file = tmp_path / "pages.gz"
    _filled(("/a", b"aaaa"), ("/b", b"bbbb")).save(file, 1 << 20)
    whole = gzip.decompress(file.read_bytes())
    file.write_bytes(gzip.compress(whole[:-2]))
    fresh = ResponseCache()
    assert fresh.load(file) == 1


def test_a_page_built_since_startup_wins_over_the_snapshot(tmp_path: Path) -> None:
    file = tmp_path / "pages.gz"
    _filled(("/", b"old")).save(file, 1 << 20)
    fresh = _filled(("/", b"new"))
    assert fresh.load(file) == 0 and fresh.peek(("/", "")) == (b"new", 200)


def test_crawlers_never_take_a_visitors_build_slot() -> None:
    import pytest

    from partylinks.api import CRAWLING, CacheBusy

    cache = ResponseCache(build_wait=0.05)
    cache._crawler_builds.acquire()
    cache._crawler_builds.acquire()  # a crawler burst holds both of its slots
    token = CRAWLING.set(True)
    try:
        with pytest.raises(CacheBusy):
            cache.get(("/artist/x", ""), lambda: b"x")  # the next crawler page waits on its own slots
    finally:
        CRAWLING.reset(token)
    assert cache.get(("/", ""), lambda: b"home") == (b"home", 200)  # a visitor is built at once


def test_link_previews_count_as_visitors() -> None:
    from partylinks.api import is_crawler

    assert is_crawler("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)")
    assert is_crawler("Mozilla/5.0 (compatible; meta-externalagent/1.1)")
    assert is_crawler("Lightpanda/1.0")
    assert not is_crawler("WhatsApp/2.23.20.0")
    assert not is_crawler("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)")
    assert not is_crawler("TelegramBot (like TwitterBot)")
    assert not is_crawler("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Safari/604.1")
