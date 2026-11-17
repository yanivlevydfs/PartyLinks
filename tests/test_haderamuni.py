"""Hadera municipality: WordPress sitemap and muni_events pages (15 November 2026) to ScrapedEvent."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import zone
from partylinks.scrapers.haderamuni import (
    HaderaMuniSettings,
    apply_terms,
    parse_event,
    parse_sitemap,
    parse_term_page,
    recent_pages,
    term_class,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/haderamuni"
LASTMOD = datetime(2026, 11, 15, 15, 38, tzinfo=zone(TZ))
SITE = "https://www.hadera.muni.il"
S = HaderaMuniSettings.model_validate(Settings().sources["haderamuni"])  # the production block


def page(post_id: str) -> str:
    return (FIXTURES / f"{post_id}.html").read_text("utf-8")


def event(post_id: str, lastmod: datetime = LASTMOD, settings: HaderaMuniSettings = S):
    found = parse_event(page(post_id), f"{SITE}/muni_events/{post_id}/", lastmod, settings, TZ)
    assert found is not None
    return found


def test_sitemap_keeps_recent_pages_only() -> None:
    xml = (FIXTURES / "sitemap-posts.xml").read_text("utf-8")
    assert len(parse_sitemap(xml)) == 9
    pages = recent_pages(xml, LASTMOD - timedelta(days=120), TZ)
    assert len(pages) == 6  # the three 2025 pages are past events, never fetched
    assert all(when.year == 2026 for when in pages.values())
    assert all(url.startswith(SITE + "/muni_events/") for url in pages)


def test_sitemap_index_and_undated_entries() -> None:
    index = (
        "<sitemapindex><sitemap><loc>https://www.hadera.muni.il/wp-sitemap-posts-muni_events-1.xml</loc></sitemap>"
        "<sitemap><loc>https://www.hadera.muni.il/wp-sitemap-taxonomies-events_category-1.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    assert [u for u, _ in parse_sitemap(index)] == [
        f"{SITE}/wp-sitemap-posts-muni_events-1.xml",
        f"{SITE}/wp-sitemap-taxonomies-events_category-1.xml",
    ]
    since = LASTMOD - timedelta(days=120)
    assert recent_pages("<url><loc>https://x/a/</loc></url>", since, TZ) == {"https://x/a/": since}


def test_show_at_the_culture_hall() -> None:
    e, tokens = event("52914")
    assert e.title == '"מה שבפנים" – הצגה מקורית, מצחיקה ומרגשת'
    assert e.source_event_id == "52914"
    assert e.start_at == datetime(2026, 11, 29, 19, 0, tzinfo=zone(TZ))
    assert e.end_at == datetime(2026, 11, 29, 21, 0, tzinfo=zone(TZ))
    assert e.time_known
    # "אולם רינה שני, חדרה" in the text; the map embed gives 'חדרה רמב"ם 39, חדרה'.
    assert (e.venue_name, e.address, e.city) == ("אולם רנה שני", 'רמב"ם 39', "חדרה")
    assert (e.min_price, e.max_price, e.tickets[0].is_free) == (Decimal(10), Decimal(10), False)
    assert e.extra["purchase_url"].startswith("https://www.pnay.org.il/page.php?type=event&id=35079")
    assert e.image_url and e.image_url.endswith("-1080x1080-v2.jpg")  # the post's artwork, not the shared banner
    assert e.source_genres == ["לכל המשפחה"]
    assert tokens == ["events_category-372"]
    assert e.description and "הרצף האוטיסטי" in e.description and "wp-content" not in e.description


def test_waze_pin_free_entry_and_a_form_link() -> None:
    e, tokens = event("52911")
    assert e.start_at == datetime(2026, 12, 1, 16, 30, tzinfo=zone(TZ))
    assert (e.latitude, e.longitude) == (pytest.approx(32.4303508), pytest.approx(34.9185097))
    assert (e.venue_name, e.address) == ("מועדון אקים", "בוטקובסקי 3")
    assert e.min_price == Decimal(0) and e.tickets[0].is_free
    assert e.extra["purchase_url"].startswith("https://akim-hadera.co.il/")
    assert e.source_genres == []  # audiences that name no kind of event (teens and up, adults, seniors)
    assert tokens == ["events_category-401", "events_category-402"]


def test_hall_line_and_street_from_the_map() -> None:
    e, _ = event("52912")
    assert (e.venue_name, e.address) == ("אולם הספורט של בית הספר צפרירים", "האלון 28")
    assert e.extra == {}  # the ticket button has no link and the text none
    assert e.start_at == datetime(2026, 12, 2, 17, 0, tzinfo=zone(TZ))


def test_place_without_a_street() -> None:
    e, tokens = event("52910")
    assert (e.venue_name, e.address) == ("מרכז ברוש", "הלל יפה 26")  # "מרכז ברוש (גן ברנדייס), חדרה"
    assert e.latitude is None  # Waze by venue id only
    assert e.min_price == Decimal(55)
    assert e.extra["purchase_url"].startswith("https://www.pnay.org.il/")
    assert tokens == ["events_category-_"]


def test_year_from_lastmod_when_the_text_has_none() -> None:
    # 52912's text gives no year: "2 דצמבר" is taken nearest the page's last change.
    e, _ = event("52912", lastmod=datetime(2027, 1, 10, tzinfo=zone(TZ)))
    assert e.start_at.year == 2026
    e, _ = event("52912", lastmod=datetime(2027, 10, 1, tzinfo=zone(TZ)))
    assert e.start_at.year == 2027
    # A "29.11.2026" in the text wins over the lastmod.
    e, _ = event("52914", lastmod=datetime(2027, 10, 1, tzinfo=zone(TZ)))
    assert e.start_at.year == 2026


def test_smarticket_box_office_sale_is_skipped() -> None:
    html = page("52910").replace(
        "https://www.pnay.org.il/page.php?type=event&#038;id=35180&#038;m=201&#038;ht=#navbar",
        "https://eventshadera.smarticket.co.il/event/123",
    )
    assert parse_event(html, f"{SITE}/x/", LASTMOD, S, TZ) is None


def test_undated_page_is_skipped_and_broken_page_raises() -> None:
    html = page("52912").replace("2 דצמבר", "")
    assert parse_event(html, f"{SITE}/x/", LASTMOD, S, TZ) is None
    with pytest.raises(Exception, match="no event block"):
        parse_event("<html><body></body></html>", f"{SITE}/x/", LASTMOD, S, TZ)


def test_term_classes_and_names() -> None:
    # Pure Hebrew slugs leave nothing ASCII: WordPress then writes the term id ("events_category-401").
    assert term_class(f"{SITE}/events_category/%d7%a1%d7%a4%d7%95%d7%a8%d7%98/") == (None, "ספורט")
    assert term_class(f"{SITE}/target_audience/%d7%a0%d7%95%d7%a2%d7%a8-13-18/") == ("-13-18", "נוער 13 18")
    assert term_class(f"{SITE}/events_category/%d7%aa%d7%a8%d7%91%d7%95%d7%aa_/") == ("_", "תרבות_")
    term_page = (
        "<html><head><title>ספורט &#8211; אתר האינטרנט</title></head>"
        '<body class="rtl archive tax-events_category term-401 elementor-default"></body></html>'
    )
    assert parse_term_page(term_page) == ("401", "ספורט")
    assert parse_term_page("<html><body class='home'></body></html>") is None


def test_terms_become_genres_and_the_venue() -> None:
    e, tokens = event("52911")
    named = apply_terms(
        e, [*tokens, "event_location-357"], {"events_category-401": "ספורט", "event_location-357": "אולם רנה שני"}, S
    )
    assert named.source_genres == ["ספורט"]
    assert named.venue_name == "אולם רנה שני"
    assert named.address == "בוטקובסקי 3"  # the page's own street stays
    e, _ = event("52910")
    named = apply_terms(e, ["event_location-358"], {"event_location-358": "היכל אנרבוקס"}, S)
    assert (named.venue_name, named.address) == ("היכל אנרבוקס", "משמר הגבול 1")


def test_every_event_gets_a_site_genre() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    for post_id in ("52910", "52911", "52912", "52914"):
        e, _ = event(post_id)
        assert enricher.enrich(e).genres, post_id
    e, _ = event("52914")
    assert "theatre" in enricher.enrich(e).genres  # "הצגה" in the title
    e, _ = event("52911")
    sport = apply_terms(e, ["events_category-401"], {"events_category-401": "ספורט"}, S)
    assert "sport" in enricher.enrich(sport).genres


def test_run_reads_recent_pages_only_and_names_their_categories() -> None:
    import asyncio

    import httpx

    from partylinks.scrapers import build_scraper
    from partylinks.scrapers.haderamuni import _MONTHS

    scraper = build_scraper("haderamuni", Settings())
    scraper.http.max_retries, scraper.http.backoff_seconds, scraper.http.request_delay_seconds = 0, 0, 0
    now = datetime.now(zone(TZ))
    soon = now + timedelta(days=10)
    month = next(name for name, n in _MONTHS.items() if n == soon.month)
    show = page("52912").replace("2 דצמבר", f"{soon.day} {month}")  # a page whose text gives no year
    sport, other = "%d7%a1%d7%a4%d7%95%d7%a8%d7%98", "%d7%aa%d7%a8%d7%91%d7%95%d7%aa"
    stamp = now.isoformat(timespec="seconds")
    files = {
        "/wp-sitemap.xml": "<sitemapindex>"
        f"<sitemap><loc>{SITE}/wp-sitemap-posts-muni_events-1.xml</loc></sitemap>"
        f"<sitemap><loc>{SITE}/wp-sitemap-posts-page-1.xml</loc></sitemap>"
        f"<sitemap><loc>{SITE}/wp-sitemap-taxonomies-events_category-1.xml</loc></sitemap>"
        f"<sitemap><loc>{SITE}/wp-sitemap-taxonomies-event_interests-1.xml</loc></sitemap></sitemapindex>",
        "/wp-sitemap-posts-muni_events-1.xml": "<urlset>"
        f"<url><loc>{SITE}/muni_events/old/</loc><lastmod>2025-01-21T12:11:36+02:00</lastmod></url>"
        f"<url><loc>{SITE}/muni_events/show/</loc><lastmod>{stamp}</lastmod></url>"
        f"<url><loc>{SITE}/muni_events/past/</loc><lastmod>{stamp}</lastmod></url></urlset>",
        "/wp-sitemap-taxonomies-events_category-1.xml": "<urlset>"
        f"<url><loc>{SITE}/events_category/{sport}/</loc></url>"
        f"<url><loc>{SITE}/events_category/{other}/</loc></url></urlset>",
        "/muni_events/show/": show,
        # "29.11.2026" in its text: past once that day is over. Same category as the show.
        "/muni_events/past/": page("52914").replace("events_category-372", "events_category-188"),
        f"/events_category/{sport}/": "<html><head><title>ספורט &#8211; אתר האינטרנט</title></head>"
        '<body class="archive tax-events_category term-188"></body></html>',
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode().split("?")[0]
        asked.append(path)
        if path in files:
            return httpx.Response(200, text=files[path], headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go() -> list:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(go())}
    assert "/muni_events/old/" not in asked  # an old lastmod: never requested
    assert "/wp-sitemap-taxonomies-event_interests-1.xml" not in asked  # no event uses that taxonomy
    assert f"/events_category/{other}/" not in asked  # the one id in use was named by the first term page
    e = events["52912"]
    assert e.start_at.date() == soon.date() and e.start_at.hour == 17
    assert e.source_genres == ["לכל המשפחה", "ספורט"]
    assert ("52914" in events) == (now.date() <= datetime(2026, 11, 29).date())
