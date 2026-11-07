"""Shotgun: a real event page (Manaus, October 2026) trimmed to its JSON-LD, and the listing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from partylinks.config import HttpSettings
from partylinks.scrapers.base import SourceBlockedError
from partylinks.scrapers.shotgun import _EVENT_LINK, ShotgunScraper, ShotgunSettings, links, parse_event, sitemap_urls

SITE = "https://shotgun.live"
PAGE = (Path(__file__).parent / "fixtures" / "shotgun" / "event_brazil.html").read_text(encoding="utf-8")
URL = f"{SITE}/en/events/intrance-24-10"


def test_event_abroad_has_its_country_and_one_in_israel_none() -> None:
    abroad = parse_event(PAGE, URL, "Asia/Jerusalem")
    assert abroad is not None and abroad.country == "BR"  # Manaus: the abroad section
    assert abroad.source == "shotgun" and abroad.source_event_id == "intrance-24-10"
    assert abroad.venue_name == "Nexus Lounge & Club" and abroad.latitude and abroad.tickets
    israel = PAGE.replace('"addressCountry":"BR"', '"addressCountry":"IL"')
    assert israel != PAGE
    event = parse_event(israel, URL, "Asia/Jerusalem")
    assert event is not None and event.country is None


def test_listing_links() -> None:
    html = (
        '<a href="/en/events/a-1">x</a><a href="/en/events/a-1">y</a><a href="https://shotgun.live/en/events/b">z</a>'
    )
    assert links(html, _EVENT_LINK, SITE) == [f"{SITE}/en/events/a-1", f"{SITE}/en/events/b"]


def test_no_israeli_pages_yet_is_an_empty_run_not_a_failure() -> None:
    settings = ShotgunSettings(
        cron="0 * * * *",
        site_url=SITE,
        country_path="/en/venues/-/israel",
        city_paths=["/en/cities/tel-aviv"],
        proxied=False,
    )
    scraper = ShotgunScraper(settings, HttpSettings(max_retries=0), "Asia/Jerusalem")
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(404, text="<title>Page not found</title>")

    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]

    async def run() -> list[object]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    assert asyncio.run(run()) == [] and asked == ["/en/venues/-/israel", "/en/cities/tel-aviv"]


def _scraper(answer, **settings: object) -> ShotgunScraper:  # type: ignore[no-untyped-def]
    scraper = ShotgunScraper(
        ShotgunSettings(cron="0 * * * *", site_url=SITE, country_path="/en/venues/-/israel", proxied=False, **settings),
        HttpSettings(max_retries=0),
        "Asia/Jerusalem",
    )
    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]
    return scraper


def _run(scraper: ShotgunScraper) -> list[object]:
    async def run() -> list[object]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    return asyncio.run(run())


def test_sitemap_urls_recent_only() -> None:
    xml = (
        "<urlset><url><loc>https://shotgun.live/en/events/new</loc>"
        '<xhtml:link rel="alternate" hreflang="fr" href="https://shotgun.live/fr/events/new" />'
        "<lastmod>2026-11-05</lastmod></url>"
        "<url><loc>https://shotgun.live/en/events/old</loc><lastmod>2026-08-01</lastmod></url>"
        "<url><loc>https://shotgun.live/en/events/none</loc></url></urlset>"
    )
    assert sitemap_urls(xml, datetime(2026, 10, 23, tzinfo=UTC)) == [f"{SITE}/en/events/new"]


def test_sitemap_events_in_the_city_pages_countries_newest_maps_first() -> None:
    soon = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    future = PAGE.replace("2026-10-24", soon).replace("2026-10-25", soon)
    today = datetime.now(UTC).date().isoformat()
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/en/cities/manaus":
            return httpx.Response(200, text='<a href="/en/events/intrance-24-10">x</a>')
        if path == "/sitemap.xml":
            maps = "".join(
                f"<sitemap><loc>{SITE}/api/sitemaps/events/sitemap/{n}.xml</loc></sitemap>" for n in range(3)
            )
            return httpx.Response(200, text=f"<sitemapindex>{maps}</sitemapindex>")
        if path == "/api/sitemaps/events/sitemap/2.xml":
            urls = ["intrance-24-10", "in-brazil", "in-france"]
            body = "".join(f"<url><loc>{SITE}/en/events/{u}</loc><lastmod>{today}</lastmod></url>" for u in urls)
            return httpx.Response(200, text=f"<urlset>{body}</urlset>")
        if path == "/en/events/in-france":
            return httpx.Response(200, text=future.replace('"addressCountry":"BR"', '"addressCountry":"FR"'))
        if path.startswith("/en/events/"):
            return httpx.Response(200, text=future)
        return httpx.Response(404)

    events = _run(_scraper(answer, city_paths=["/en/cities/manaus"], event_sitemaps=1))
    assert "BR" in PAGE and soon in future
    assert sorted(e.source_event_id for e in events) == ["in-brazil", "intrance-24-10"]  # type: ignore[attr-defined]
    assert "/api/sitemaps/events/sitemap/1.xml" not in asked  # the newest one only
    assert asked.count("/en/events/intrance-24-10") == 1  # listed already: not fetched again


def test_sitemaps_read_in_batches_each_event_page_once() -> None:
    soon = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    future = PAGE.replace("2026-10-24", soon).replace("2026-10-25", soon)
    today = datetime.now(UTC).date().isoformat()
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/en/cities/manaus":
            return httpx.Response(200, text='<a href="/en/events/intrance-24-10">x</a>')
        if path == "/sitemap.xml":
            maps = "".join(
                f"<sitemap><loc>{SITE}/api/sitemaps/events/sitemap/{n}.xml</loc></sitemap>" for n in range(3)
            )
            return httpx.Response(200, text=f"<sitemapindex>{maps}</sitemapindex>")
        if path.startswith("/api/sitemaps/events/sitemap/"):  # every sitemap lists the same new page
            body = f"<url><loc>{SITE}/en/events/in-brazil</loc><lastmod>{today}</lastmod></url>"
            return httpx.Response(200, text=f"<urlset>{body}</urlset>")
        if path.startswith("/en/events/"):
            return httpx.Response(200, text=future)
        return httpx.Response(404)

    events = _run(_scraper(answer, city_paths=["/en/cities/manaus"], event_sitemaps=3, sitemaps_per_batch=1))
    assert sorted(e.source_event_id for e in events) == ["in-brazil", "intrance-24-10"]  # type: ignore[attr-defined]
    assert sum(p.startswith("/api/sitemaps/events/") for p in asked) == 3
    assert asked.count("/en/events/in-brazil") == 1  # one batch read it: the next ones skip it


def test_still_429_stops_the_run_cleanly() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/en/cities/a":
            return httpx.Response(200, text='<a href="/en/events/e1">x</a><a href="/en/events/e2">y</a>')
        return httpx.Response(429, text="<title>Vercel Security Checkpoint</title>")

    scraper = _scraper(answer, city_paths=["/en/cities/a"], sitemap_index_path=None)
    try:
        _run(scraper)
    except SourceBlockedError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("a persistent 429 must stop the run")
    assert not any("closed" in f for f in scraper.failures)
