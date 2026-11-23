import asyncio
from datetime import UTC, datetime
from typing import Any

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent
from partylinks.scrapers.eventer import EventerScraper, EventerSettings
from partylinks.scrapers.eventer_sitemap import disallowed, is_over, link_name, public_names


def _sitemap(*names: str) -> str:
    return "<urlset>" + "".join(f"<url><loc>https://www.eventer.co.il/{n}</loc></url>" for n in names) + "</urlset>"


def test_link_names_from_sitemap_urls() -> None:
    assert link_name("https://www.eventer.co.il/piza") == "piza"
    assert link_name("https://www.eventer.co.il/events/piza") == "piza"
    assert link_name("https://www.eventer.co.il") is None
    assert link_name("https://www.eventer.co.il/%D7%A9%D7%99%D7%A8%D7%95%D7%AA%D7%99%D7%9D/x") is None  # a service page


def test_robots_disallow_only_for_every_crawler() -> None:
    robots = "User-agent: Facebot\nDisallow: /fb\nUser-agent: *\nDisallow:\nDisallow: /p/*\nDisallow: /secret\nSitemap: https://x/s.xml"
    assert disallowed(robots) == ["secret"]


class _Pages:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages, self.asked = pages, []

    async def get_text(self, url: str) -> str:
        self.asked.append(url)
        return self.pages[url]


def test_public_names_newest_first_skip_private_dedupe_and_read_files_lazily() -> None:
    fake = _Pages({
        "https://x/sitemap.xml": "<loc>https://x/s1.xml</loc><loc>https://x/s2.xml</loc>",
        "https://x/s1.xml": _sitemap("old1", "old2"),
        "https://x/s2.xml": _sitemap("a", "events/a", "secret", "b", "piza"),
    })

    async def first(n: int) -> list[str]:
        names = public_names(fake, "https://x", "/sitemap.xml", {"secret"})  # type: ignore[arg-type]
        return [x async for x in names][:n] if n > 3 else [await anext(names) for _ in range(n)]

    assert asyncio.run(first(3)) == ["piza", "b", "a"]
    assert "https://x/s1.xml" not in fake.asked  # the older file was never needed
    assert asyncio.run(first(10)) == ["piza", "b", "a", "old2", "old1"]


def test_is_over_uses_end_or_three_hours_after_start() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    assert is_over({"schedule": {"start": "2026-10-01 20:00", "end": "2026-10-01 23:00"}}, "Asia/Jerusalem", now)
    assert not is_over({"schedule": {"start": "2026-11-19 20:00", "end": "2026-11-19 22:00"}}, "Asia/Jerusalem", now)
    assert not is_over({"schedule": {"start": "2026-11-01 13:00"}}, "Asia/Jerusalem", now)  # 11:00 UTC + 3 h
    assert not is_over({"schedule": {}}, "Asia/Jerusalem", now)


def test_a_deleted_event_is_gone_not_a_block() -> None:
    import httpx

    from partylinks.scrapers.eventer import EventerScraper

    gone = httpx.Response(403, content=b'{"message":"Event does not exist"}')
    assert EventerScraper.is_gone(None, gone)  # type: ignore[arg-type]
    assert not EventerScraper.is_gone(None, httpx.Response(403, content=b"<html>Access denied</html>"))  # type: ignore[arg-type]


def _scraper(**raw: Any) -> EventerScraper:
    settings = EventerSettings.model_validate({
        "cron": "0 1 * * *", "base_url": "https://x", "list_path": "/l/{category}", "event_details_path": "/e/{link_name}",
        "ticket_types_path": "/t/{event_id}", "discover": False, "sitemap_path": "/sitemap.xml", **raw,
    })
    return EventerScraper(settings, HttpSettings(), "Asia/Jerusalem")


def _run(scraper: EventerScraper, pages: dict[str, Any], current: set[str]) -> tuple[list[str], list[str]]:
    """The names whose page the scraper read, and the events it yielded (``current``: the ones not over)."""
    asked: list[str] = []

    async def get_text(url: str, **_: Any) -> str:
        return pages[url]

    async def get_json(url: str, **_: Any) -> Any:
        return pages[url]

    async def fetch(name: str) -> ScrapedEvent | None:
        asked.append(name)
        return ScrapedEvent.model_construct(source_event_id=name) if name in current else None  # pyrefly: ignore[missing-argument]  (a bare stand-in: only the id is read)

    scraper.get_text, scraper.get_json, scraper._fetch_event = get_text, get_json, fetch  # type: ignore[method-assign]

    async def go() -> list[str]:
        return [e.source_event_id async for e in scraper.scrape()]

    return asked, asyncio.run(go())


def test_private_newest_then_the_sitemap_on_until_a_gap_with_no_current_event() -> None:
    names = [f"p{i}" for i in range(30)]  # p29 newest
    pages = {
        "https://x/robots.txt": "User-agent: *\nDisallow: /old_secret\nDisallow: /new_secret",
        "https://x/sitemap.xml": "<loc>https://x/s1.xml</loc>",
        "https://x/s1.xml": _sitemap("old_secret", *names),
    }
    scraper = _scraper(sitemap_newest=4, sitemap_gap=6, sitemap_batch=3, private_newest=1)
    # p20 (page 10 from the newest) is still on sale: the reading goes on 6 pages past it, then stops.
    asked, found = _run(scraper, pages, {"p28", "p20"})
    assert asked[:5] == ["new_secret", "p29", "p28", "p27", "p26"]
    assert asked[5:] == [f"p{i}" for i in range(25, 13, -1)]  # 25..23, 22..20 (hit), 19..17, 16..14: 6 empty
    assert sorted(found) == ["p20", "p28"]


def test_no_gap_stops_at_the_newest() -> None:
    pages = {
        "https://x/robots.txt": "",
        "https://x/sitemap.xml": "<loc>https://x/s1.xml</loc>",
        "https://x/s1.xml": _sitemap("a", "b", "c"),
    }
    asked, _ = _run(_scraper(sitemap_newest=2, private_newest=0), pages, {"a"})
    assert asked == ["c", "b"]


def test_container_pages_and_venue_pages_add_their_events() -> None:
    pages = {
        "https://x/eventContainer/Production/listByType.js": {"slides": [{"linkName": "kolben"}, {"title": "no link"}]},
        "https://x/Production/kolben/byLink.js": {"events": [{"linkName": "34s2f"}, {"name": "no link"}]},
    }
    scraper = _scraper(sitemap_path="", containers=["Production"])
    scraper.venue_events = ("barby1",)
    asked, _ = _run(scraper, pages, set())
    assert asked == ["barby1", "34s2f"]
