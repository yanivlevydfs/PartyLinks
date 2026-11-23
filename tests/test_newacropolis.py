"""New Acropolis: the Wix Events sitemap's recent pages, each page's JSON-LD Event, branch places, skipped sessions."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.newacropolis import (
    NewAcropolisScraper,
    NewAcropolisSettings,
    listed_pages,
    parse_event,
    place,
    recent_pages,
)

SITE = "https://www.newacropolis.org.il"
SOON = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0)


def _settings() -> NewAcropolisSettings:
    s = build_scraper("newacropolis", Settings()).settings
    assert isinstance(s, NewAcropolisSettings)
    return s


def _page(**over: Any) -> str:
    """An event page's JSON-LD, as Wix Events writes it (Nov 2026)."""
    node = {
        "@context": "https://schema.org", "@type": "Event", "name": "חיים. משמעות. כיוון. דרך.",
        "description": "הרצאה פתוחה", "startDate": SOON.isoformat(), "endDate": (SOON + timedelta(hours=2)).isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "location": {"@type": "Place", "url": SITE + "/event-details/x", "address": "ברשבסקי 10 ראשון לציון"},
        "image": {"@type": "ImageObject", "url": "https://static.wixstatic.com/media/d7a49f_1cd3~mv2.jpg/v1/fill/w_860,h_450/a.jpg"},
    } | over  # fmt: skip
    return f'<html><script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script></html>'


def test_only_recently_updated_event_pages_newest_first() -> None:
    xml = (
        f"<urlset><url><loc>{SITE}/event-details/old</loc><lastmod>2024-09-10</lastmod></url>"
        f"<url><loc>{SITE}/event-details/a</loc><lastmod>2026-11-18</lastmod></url>"
        f"<url><loc>{SITE}/event-details/b</loc><lastmod>2026-11-22</lastmod></url>"
        f"<url><loc>{SITE}/about</loc><lastmod>2026-11-22</lastmod></url></urlset>"
    )
    since = datetime(2026, 9, 1, tzinfo=UTC)
    assert recent_pages(xml, since) == [f"{SITE}/event-details/b", f"{SITE}/event-details/a"]


def test_a_recurring_series_coming_occurrences_despite_an_old_lastmod() -> None:
    # Nei Kung's monthly evening (Nov 2026): every occurrence's page keeps the series' Dec 2025 lastmod.
    xml = (
        f"<urlset><url><loc>{SITE}/event-details/nyy-qwng-kwh-pnymy-btnwh-2026-11-16-17-45</loc><lastmod>2025-12-27</lastmod></url>"
        f"<url><loc>{SITE}/event-details/nyy-qwng-kwh-pnymy-btnwh-2026-12-14-17-45</loc><lastmod>2025-12-27</lastmod></url>"
        f"<url><loc>{SITE}/event-details/nyy-qwng-ymwn-ptwh-2</loc><lastmod>2024-10-25</lastmod></url>"
        f"<url><loc>{SITE}/event-details/a</loc><lastmod>2026-11-18</lastmod></url></urlset>"
    )  # fmt: skip
    since = datetime(2026, 9, 1, tzinfo=UTC)
    assert recent_pages(xml, since, date(2026, 11, 24)) == [
        f"{SITE}/event-details/a", f"{SITE}/event-details/nyy-qwng-kwh-pnymy-btnwh-2026-12-14-17-45",
    ]  # fmt: skip


def test_the_events_page_links_each_once_without_share_links() -> None:
    html = (
        f'<a href="{SITE}/event-details/boutique-fair-rishon-lezion">x</a>'
        f'<a href="https://wa.me/?text={SITE}/event-details/boutique-fair-rishon-lezion&amp;text=Check">s</a>'
        f'<a href="{SITE}/event-details/nyy-qwng-2026-12-14-17-45?lang=he">y</a>'
    )
    assert listed_pages(html, SITE) == [
        f"{SITE}/event-details/boutique-fair-rishon-lezion", f"{SITE}/event-details/nyy-qwng-2026-12-14-17-45",
    ]  # fmt: skip


def test_places_from_the_address_text_and_the_branches() -> None:
    s = _settings()
    assert place("ברשבסקי 10 ראשון לציון", None, s) == (
        "אקרופוליס החדשה ראשון לציון",
        "ברשבסקי 10 ראשון לציון",
        "ראשון לציון",
    )
    assert place(" סלומון 7, תל אביב", "בית אקרופוליס החדשה - תל אביב", s) == (
        "בית אקרופוליס החדשה - תל אביב", "סלומון 7", "תל אביב-יפו",
    )  # fmt: skip
    # Not a branch: the text's own parts (the country dropped), the location's name when it is not the city.
    assert place("רבי עקיבא 15, פרדס חנה כרכור, ישראל", "פרדס חנה כרכור", s) == (
        "רבי עקיבא 15", "רבי עקיבא 15", "פרדס חנה כרכור",
    )  # fmt: skip


def test_event_page() -> None:
    url = SITE + "/event-details/hyym-msmwt-kywwn-drk-2"
    event = parse_event(_page(), url, _settings())
    assert event is not None
    assert (event.source, event.source_event_id, event.title) == (
        "newacropolis",
        "hyym-msmwt-kywwn-drk-2",
        "חיים. משמעות. כיוון. דרך.",
    )
    assert event.start_at == SOON and event.end_at == SOON + timedelta(hours=2)
    assert (event.venue_name, event.city) == ("אקרופוליס החדשה ראשון לציון", "ראשון לציון")
    assert event.images == ["https://static.wixstatic.com/media/d7a49f_1cd3~mv2.jpg/v1/fill/w_860,h_450/a.jpg"]
    enriched = Enricher(Settings().enrichment, "Asia/Jerusalem").enrich(event)
    assert enriched is not None and enriched.genres  # lectures, at the least (the source's default)


def test_cancelled_and_eventless_pages_give_nothing() -> None:
    s, url = _settings(), SITE + "/event-details/x"
    assert parse_event(_page(eventStatus="https://schema.org/EventCancelled"), url, s) is None
    assert parse_event(_page(startDate=None), url, s) is None
    assert parse_event("<html></html>", url, s) is None


def test_run_drops_the_course_sessions_and_volunteering() -> None:
    scraper = build_scraper("newacropolis", Settings())
    assert isinstance(scraper, NewAcropolisScraper)
    scraper.http.request_delay_seconds = 0
    today = datetime.now(UTC).date().isoformat()
    pages = {
        "talk": _page(),
        "course": _page(name="קורס פילוסופיה בשביל החיים"),  # the course's own session
        "open": _page(name="מפגש היכרות פתוח לקורס פילוסופיה בשביל החיים"),  # its open evening: kept
        "volunteer": _page(name="עושים תלפיות - התנדבות"),
    }
    xml = (
        "<urlset>"
        + "".join(f"<url><loc>{SITE}/event-details/{k}</loc><lastmod>{today}</lastmod></url>" for k in pages)
        + "</urlset>"
    )

    listing = f'<a href="{SITE}/event-details/listed">x</a><a href="{SITE}/event-details/talk">t</a>'
    pages["listed"] = _page(name="ניי קונג - כוח פנימי בתנועה")  # on the events page, its sitemap lastmod old

    def handler(request: httpx.Request) -> httpx.Response:
        if "event-details" not in request.url.path and not request.url.path.endswith(".xml"):
            return httpx.Response(200, text=listing, headers={"content-type": "text/html"})
        if request.url.path.endswith(".xml"):
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})
        return httpx.Response(
            200, text=pages[request.url.path.rsplit("/", 1)[-1]], headers={"content-type": "text/html"}
        )

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]  # events(): with skip_title_words, as a run stores them

    ids = sorted(e.source_event_id for e in asyncio.run(go()))
    assert ids == ["listed", "open", "talk"] and not scraper.failures
