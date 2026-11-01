from datetime import UTC, datetime

from partylinks.scrapers.eventer_sitemap import disallowed, is_over, link_name, newest_names


def _sitemap(*names: str) -> str:
    return "<urlset>" + "".join(f"<url><loc>https://www.eventer.co.il/{n}</loc></url>" for n in names) + "</urlset>"


def test_link_names_from_sitemap_urls() -> None:
    assert link_name("https://www.eventer.co.il/piza") == "piza"
    assert link_name("https://www.eventer.co.il/events/piza") == "piza"
    assert link_name("https://www.eventer.co.il") is None
    assert link_name("https://www.eventer.co.il/%D7%A9%D7%99%D7%A8%D7%95%D7%AA%D7%99%D7%9D/x") is None  # a service page


def test_robots_disallow_only_for_every_crawler() -> None:
    robots = "User-agent: Facebot\nDisallow: /fb\nUser-agent: *\nDisallow:\nDisallow: /p/*\nDisallow: /secret\nSitemap: https://x/s.xml"
    assert disallowed(robots) == {"secret"}


def test_newest_names_read_from_the_end_skip_private_and_dedupe() -> None:
    files = [_sitemap("old1", "old2"), _sitemap("a", "events/a", "secret", "b", "piza")]
    assert newest_names(files, 3, {"secret"}) == ["piza", "b", "a"]
    assert newest_names(files, 10, {"secret"}) == ["piza", "b", "a", "old2", "old1"]


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
