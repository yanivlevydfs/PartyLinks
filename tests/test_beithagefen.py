"""Beit HaGefen: the house's and the "חג החגים" festival's AllEvents cards and item pages -> one event per performance."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError
from partylinks.scrapers import build_scraper
from partylinks.scrapers.beithagefen import (
    BeitHagefenScraper,
    BeitHagefenSettings,
    Card,
    category_pages,
    listed_item_ids,
    one_page_per_show,
    parse_event,
    parse_listing,
)

TZ = "Asia/Jerusalem"
# Pages as served on 23.11.2026, cut to the cards / the item block (no header, footer or scripts).
FIXTURES = Path(__file__).parent / "fixtures/beithagefen"
SETTINGS = BeitHagefenSettings(
    cron="0 0 * * *",
    site_url="https://www.beit-hagefen.com",
    sites=[
        {"key": "beit-hagefen", "site_url": "https://www.beit-hagefen.com", "listing_path": "/AllEvents.aspx?lang=1", "genres": ["תרבות"]},
        {"key": "haifahag", "site_url": "https://haifahag.com", "listing_path": "/AllEvents.aspx?lang=1", "genres": ["פסטיבל", "חג החגים"]},
    ],
    venue_name="בית הגפן",
    address="שדרות הציונות 33",
    city="חיפה",
    latitude=32.8165559,
    longitude=34.9948903,
    organizer="בית הגפן",
    house_words=["בית הגפן", "אלכרמה"],
    placeholder_venues=["מיקום יימסר לאחר רכישת הכרטיס", "רחבי העיר"],
    generic_categories=["כל האירועים", "אירועים קרובים", "אירועים שחלפו"],
    social_hosts=["linktr.ee", "instagram.com", "facebook.com"],
)  # fmt: skip
HOUSE, FESTIVAL = SETTINGS.sites


def _cards(site_name: str) -> dict[str, Card]:
    site = HOUSE if site_name == "house" else FESTIVAL
    page = (FIXTURES / f"{site_name}_listing.html").read_text("utf-8")
    return {c.item_id: c for c in parse_listing(page, site, tuple(SETTINGS.generic_categories))}


def _events(site_name: str, item_id: str) -> list[ScrapedEvent]:
    page = (FIXTURES / f"{site_name}_{item_id}.html").read_text("utf-8")
    return parse_event(page, _cards(site_name)[item_id], SETTINGS, TZ)


def _genres(event: ScrapedEvent) -> set[str]:
    enriched = Enricher(Settings().enrichment, TZ).enrich(event.model_copy(deep=True))
    assert enriched is not None
    return set(enriched.genres)


def test_listing_cards_take_their_category_from_the_filter_buttons() -> None:
    cards = _cards("haifahag")
    assert list(cards) == ["1383", "1664", "1656", "1600", "1663", "1645", "1633"]
    market = cards["1383"]
    assert market.url == "https://haifahag.com/EventDetalis.aspx?EventItemId=1383&CategoryItemId=5"
    assert (market.title, market.first_day.isoformat() if market.first_day else None) == (
        "השוק מתעורר | אמנות וטעימות מקומיות", "2026-12-04",
    )  # fmt: skip
    assert market.image == "https://haifahag.com/images/3128_EventItem_R1.png"
    assert (market.categories, cards["1600"].categories, cards["1656"].categories) == (
        ["סיורים"],
        ["מתחם משפחות"],
        ["מופעים"],
    )
    assert cards["1633"].categories == []  # category 7's button reads "כל האירועים": no category
    assert all(c.categories == [] for c in _cards("house").values())  # "אירועים קרובים" is generic too


def test_each_named_category_has_its_filtered_page() -> None:
    page = (FIXTURES / "haifahag_listing.html").read_text("utf-8")
    assert category_pages(page, FESTIVAL, tuple(SETTINGS.generic_categories)) == {
        "מופעים": "https://haifahag.com/AllEvents.aspx?CategoryItemId=2",
        "מתחם משפחות": "https://haifahag.com/AllEvents.aspx?CategoryItemId=6",
        "תערוכות": "https://haifahag.com/AllEvents.aspx?CategoryItemId=1",
        "סיורים": "https://haifahag.com/AllEvents.aspx?CategoryItemId=5",
        "קונצרטים": "https://haifahag.com/AllEvents.aspx?CategoryItemId=3",
    }  # "כל האירועים" (8 and 7) and the past events are no category
    assert listed_item_ids(page) == {"1383", "1600", "1633", "1645", "1656", "1663", "1664"}
    house = (FIXTURES / "house_listing.html").read_text("utf-8")
    assert list(category_pages(house, HOUSE, tuple(SETTINGS.generic_categories))) == ["גלריה בית הגפן", "תרבות"]


def test_show_in_a_hall_of_the_house() -> None:
    [show] = _events("haifahag", "1656")
    assert show.title == "כֶּשֶׁף | קמא קמילה והלהקה"
    assert show.source_event_id == "haifahag:1656:202612112130"
    assert show.start_at.isoformat() == "2026-12-11T21:30:00+02:00" and show.time_known and show.end_at is None
    assert (show.venue_name, show.address, show.city) == ("בית הגפן", "שדרות הציונות 33", "חיפה")
    assert (show.latitude, show.longitude) == (32.8165559, 34.9948903)  # the house's pin for every hall
    assert show.extra == {"hall": "אולם בית הגפן", "purchase_url": "https://www.eventer.co.il/hoh26kamakamila"}
    assert (show.min_price, show.max_price, [t.price for t in show.tickets]) == (
        Decimal(50),
        Decimal(50),
        [Decimal(50)],
    )
    assert show.image_url == "https://haifahag.com/images/3130_EventItem_R1.png" and show.images == [show.image_url]
    assert show.source_genres == ["מופעים", "פסטיבל", "חג החגים"] and show.organizer == "בית הגפן"
    assert show.description and show.description.startswith("קול עמוק")  # the title line it opens with is dropped
    assert {"live_show", "festival"} <= _genres(show)


def test_one_event_per_performance() -> None:
    workshops = _events("haifahag", "1633")
    assert [e.start_at.isoformat() for e in workshops] == ["2026-12-18T13:00:00+02:00", "2026-12-18T15:00:00+02:00"]
    assert len({e.source_event_id for e in workshops}) == 2
    assert {e.extra["purchase_url"] for e in workshops} == {"https://www.eventer.co.il/hoh26kintzugiws"}
    assert workshops[0].extra["hall"] == "סטודיו אלכרמה | קהילת יוצרים.ות" and workshops[0].venue_name == "בית הגפן"
    assert "workshops" in _genres(workshops[0])  # no category of its own: the title's "סדנת"
    tours = _events("haifahag", "1383")
    assert [e.start_at.date().isoformat() for e in tours] == ["2026-12-04", "2026-12-11", "2026-12-18"]
    # The meeting point is "given after buying": placed at the house, as Eventer places it.
    assert (tours[0].venue_name, tours[0].latitude, "hall" in tours[0].extra) == ("בית הגפן", 32.8165559, False)
    assert {"tours", "festival"} <= _genres(tours[0])


def test_free_show_in_the_yard() -> None:
    [circus] = _events("haifahag", "1600")
    assert (circus.min_price, circus.max_price) == (Decimal(0), Decimal(0))
    assert [(t.name, t.is_free) for t in circus.tickets] == [("כניסה חופשית", True)]
    assert circus.extra == {"hall": "חצר בית הגפן"}  # sold nowhere: no ticket link
    enriched = Enricher(Settings().enrichment, TZ).enrich(circus.model_copy(deep=True))
    assert enriched is not None and enriched.has_free_entry and {"kids", "circus_magic"} <= set(enriched.genres)


def test_venue_outside_the_house_keeps_its_own_pin_and_a_social_link_is_dropped() -> None:
    [flamenco] = _events("haifahag", "1663")
    assert (flamenco.venue_name, flamenco.address, flamenco.city) == ("קנטרה CANTERA", "שיבת ציון 21", "חיפה")
    assert (round(flamenco.latitude or 0, 4), round(flamenco.longitude or 0, 4)) == (32.8163, 34.9992)
    assert "purchase_url" not in flamenco.extra  # its button is a linktr.ee page
    [tribute] = _events("haifahag", "1664")  # no map on the page: the address is geocoded
    assert (tribute.venue_name, tribute.address, tribute.latitude) == (
        "אודיטוריום / רפפורט",
        "מרכז הכרמל, שדרות הנשיא 138",
        None,
    )
    assert tribute.extra["purchase_url"] == "https://tickchak.co.il/113162"


def test_a_show_on_both_sites_is_kept_from_the_house_site() -> None:
    [house] = _events("house", "1552")
    assert house.start_at.isoformat() == "2026-12-15T20:00:00+02:00"  # the festival page says 20:30
    assert (house.title, house.source_genres) == ('"אור" לושה גבריאלוב ~ קבוצת דוואי', ["תרבות"])
    assert house.extra["purchase_url"] == "https://davai-group.org/event/light-15-12-haifa-gefen"
    [festival] = _events("haifahag", "1645")
    workshops = _events("haifahag", "1633")
    kept = one_page_per_show([festival, house, *workshops], SETTINGS, TZ)
    assert [e.source_event_id for e in kept] == [house.source_event_id, *(w.source_event_id for w in workshops)]


def test_page_without_a_date_or_an_item_block() -> None:
    card = _cards("haifahag")["1656"]
    page = (FIXTURES / "haifahag_1656.html").read_text("utf-8").replace("11-12-2026 - 21:30", "")
    assert parse_event(page, card, SETTINGS, TZ) == []
    with pytest.raises(ParseError):
        parse_event("<html><body><p>תקלה</p></body></html>", card, SETTINGS, TZ)


def test_run_reads_both_listings_and_yields_soonest_first() -> None:
    scraper = BeitHagefenScraper(SETTINGS, Settings().http, TZ)
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        prefix = "house" if "beit-hagefen" in request.url.host else "haifahag"
        category = parse_qs(request.url.query.decode()).get("CategoryItemId", [""])[0]
        if request.url.path.endswith("AllEvents.aspx") and category:
            if prefix == "house" and category == "1":
                return httpx.Response(400)  # one category page failing costs only its label
            items = {"3": ["1664"], "6": ["1633", "1600"]}.get(category, []) if prefix == "haifahag" else []
            cards = "".join(
                f'<li class="item"><a class="item_container" href="EventDetalis.aspx?EventItemId={i}&CategoryItemId=7">'
                "</a></li>"
                for i in items
            )
            return httpx.Response(200, text=f"<ul>{cards}</ul>", headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path.endswith("AllEvents.aspx"):
            name = f"{prefix}_listing.html"
        else:
            name = f"{prefix}_{parse_qs(request.url.query.decode())['EventItemId'][0]}.html"
        return httpx.Response(
            200, text=(FIXTURES / name).read_text("utf-8"), headers={"content-type": "text/html; charset=utf-8"}
        )

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert len(set(asked)) == 2 + (2 + 5) + (2 + 7)  # two listings, each named category's page, one page per item
    assert len(scraper.failures) == 1 and scraper.failures[0].startswith("category גלריה בית הגפן")
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past ones (once the fixture's dates pass) are left out
    assert len(events) <= 11 and not any(e.source_event_id.startswith("haifahag:1645:") for e in events)
    genres = {e.source_event_id.split(":")[1]: e.source_genres for e in events}
    if "1664" in genres:  # its card names מופעים; the concerts page lists it too
        assert genres["1664"] == ["מופעים", "קונצרטים", "פסטיבל", "חג החגים"]
    if "1633" in genres:  # its card names "כל האירועים" only; the families page lists it
        assert genres["1633"] == ["מתחם משפחות", "פסטיבל", "חג החגים"]


def test_config_block() -> None:
    scraper = build_scraper("beithagefen", Settings())
    s = scraper.settings
    assert isinstance(scraper, BeitHagefenScraper) and isinstance(s, BeitHagefenSettings)
    assert [site.key for site in s.sites] == ["beit-hagefen", "haifahag"]  # the house's own pages first
    assert (s.venue_name, s.city) == ("בית הגפן", "חיפה") and s.latitude and s.longitude
