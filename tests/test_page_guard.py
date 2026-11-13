"""Pages per IP (partylinks.page_guard): a scraper can't copy every event page; search engines that check out
in DNS can; the trap link bans who follows it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from partylinks import page_guard
from partylinks.api import create_app
from tests.test_storage_api import settings_for

GOOGLE = {"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_for(tmp_path)
    settings.security.page_rate_limit_per_minute = 3
    page_guard._verified.clear()
    return create_app(settings, start_scheduler=False)


def test_pages_are_limited_per_address(app) -> None:  # type: ignore[no-untyped-def]
    with TestClient(app, client=("198.51.100.7", 1)) as c, TestClient(app, client=("198.51.100.8", 1)) as other:
        assert [c.get("/about").status_code for _ in range(4)] == [200, 200, 200, 429]
        assert c.get("/categories").status_code == 200  # JSON: its own limits, not the pages'
        assert other.get("/about").status_code == 200  # another address has its own share


def test_a_fake_googlebot_is_limited_and_a_real_one_is_not(app, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    real = {"66.249.66.1": "crawl-66-249-66-1.googlebot.com"}
    monkeypatch.setattr(page_guard.socket, "gethostbyaddr", lambda ip: (real.get(ip, "evil.example.net"), [], [ip]))
    monkeypatch.setattr(page_guard.socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", ("66.249.66.1", 0))])
    with TestClient(app, client=("66.249.66.1", 1)) as google, TestClient(app, client=("198.51.100.9", 1)) as fake:
        assert all(google.get("/about", headers=GOOGLE).status_code == 200 for _ in range(6))
        assert [fake.get("/about", headers=GOOGLE).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_the_trap_link_bans_who_follows_it(app) -> None:  # type: ignore[no-untyped-def]
    with TestClient(app, client=("198.51.100.10", 1)) as c:
        page = c.get("/about").text
        assert 'href="/archive/all-events" rel="nofollow" hidden' in page
        assert "Disallow: /archive/all-events" in c.get("/robots.txt").text
        assert c.get("/archive/all-events").status_code == 410
        assert c.get("/about").status_code == 403  # banned
