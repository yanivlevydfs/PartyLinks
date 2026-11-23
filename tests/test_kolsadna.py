"""Kol Sadna (kolsadna.co.il): open, dated workshop sessions from each workshop page's booking script."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.kolsadna import KolSadnaScraper, KolSadnaSettings, page_var, parse_workshop

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
# The booking script, JSON-LD and og:image of a real workshop page (יום תרגול ויפאסנה, Nov 2026), phone removed.
PAGE = (Path(__file__).parent / "fixtures" / "kolsadna" / "workshop.html").read_text(encoding="utf-8")
SETTINGS = KolSadnaSettings(cron="33 10,22 * * *", towns=["פרדס חנה"])
ROW = {"id": 37353, "link": "https://kolsadna.co.il/סדנה/vipassana/", "kind": [33, 29]}
KINDS = {33: "סדנאות העצמה והתפתחות אישית", 29: "סדנאות מדיטציה"}
SCHEDULE = '"occurrences":[{"date":"2026-12-18","start":"09:30","end":"16:30"}]'


def with_page(**changes: str) -> str:
    """The fixture with some of its booking variables replaced (JSON values)."""
    page = PAGE
    for name, value in changes.items():
        start = page.index(f"var {name} = ") + len(f"var {name} = ")
        end = page.index(";var ", start) if ";var " in page[start:] else page.index(";</script>", start)
        page = page[:start] + value + page[end:]
    return page


def test_an_open_session_is_an_event() -> None:
    [event] = parse_workshop(ROW, PAGE, KINDS, SETTINGS, TZ)
    assert event.title == "יום תרגול ויפאסנה בנווה שלום"
    assert event.source_event_id == "37353-202612180930"
    assert (event.start_at, event.end_at) == (
        datetime(2026, 12, 18, 9, 30, tzinfo=IL),
        datetime(2026, 12, 18, 16, 30, tzinfo=IL),
    )
    assert (event.venue_name, event.address) == ("המרכז הרוחני נווה שלום", "המרכז הרוחני נווה שלום")
    assert event.source_genres == ["סדנה", "סדנאות העצמה והתפתחות אישית", "סדנאות מדיטציה"]
    assert (event.min_price, event.max_price) == (Decimal(180), Decimal(180))
    assert event.tickets[0].price == Decimal(180)
    assert event.images and event.images[0].startswith("https://kolsadna.co.il/wp-content/uploads/")
    assert event.description and event.description.startswith("לפעמים מתרגלים מדיטציה")
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and {"workshops", "meditation"} <= set(enriched.genres)


def test_group_only_virtual_or_closed_workshops_give_nothing() -> None:
    assert parse_workshop(ROW, with_page(a_partici_type='"can_schedule"'), KINDS, SETTINGS, TZ) == []
    assert parse_workshop(ROW, with_page(a_virtual='"1"'), KINDS, SETTINGS, TZ) == []
    closed = with_page(a_schedule="{" + SCHEDULE + ',"canJoin":false}')
    assert parse_workshop(ROW, closed, KINDS, SETTINGS, TZ) == []
    assert parse_workshop(ROW, "<html>no booking script</html>", KINDS, SETTINGS, TZ) == []


def test_a_town_alone_is_the_city_not_a_venue_and_repeated_sessions_are_one() -> None:
    sessions = '{"occurrences":[{"date":"2026-12-13","start":"19:30","end":"22:30"},{"date":"2026-12-13","start":"19:30","end":"22:30"},{"date":"2026-12-20","start":"","end":""},{"date":"bad"}],"canJoin":true}'
    for town in ("מודיעין", "פרדס חנה"):
        events = parse_workshop(ROW, with_page(a_location=json.dumps(town), a_schedule=sessions), KINDS, SETTINGS, TZ)
        assert [(e.venue_name, e.address, e.city) for e in events] == [(None, None, town)] * 2
        assert [e.time_known for e in events] == [True, False]
        assert events[1].end_at is None


def test_page_var_reads_json_values() -> None:
    assert page_var(PAGE, "a_partici_type") == "can_join"
    assert page_var(PAGE, "a_schedule")["occurrences"][0]["date"] == "2026-12-18"
    assert page_var(PAGE, "a_missing") is None


def test_run_lists_recent_workshops_and_reads_their_pages() -> None:
    scraper = build_scraper("kolsadna", Settings())
    assert isinstance(scraper, KolSadnaScraper)
    assert scraper.settings.skip_title_words and scraper.settings.recent_days
    scraper.http.request_delay_seconds = 0
    soon = (datetime.now(IL).date() + timedelta(days=12)).isoformat()
    page = with_page(
        a_schedule='{"occurrences":[{"date":"' + soon + '","start":"09:30","end":"16:30"}],"canJoin":true}'
    )
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        if request.url.path == "/wp-json/wp/v2/course":
            assert request.url.params["orderby"] == "modified" and "modified_after" in request.url.params
            rows = [ROW, {**ROW, "id": 2, "link": "https://kolsadna.co.il/סדנה/groups/"}]
            return httpx.Response(200, json=rows, headers={"X-WP-Total": "2"})
        if request.url.path == "/wp-json/wp/v2/kind":
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in KINDS.items()])
        if request.url.path == "/wp-json/wp/v2/location":
            return httpx.Response(200, json=[{"id": 50, "name": "ירושלים"}])
        if "groups" in request.url.path:
            return httpx.Response(200, text=with_page(a_partici_type='"can_schedule"'))
        return httpx.Response(200, text=page)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert len(asked) == 5  # the list, the kinds, the areas, two pages
    assert [e.source_event_id for e in events] == [f"37353-{soon.replace('-', '')}0930"]
    assert not scraper.failures


def test_a_street_only_place_takes_the_workshops_one_town() -> None:
    settings = KolSadnaSettings(
        cron="33 10,22 * * *", regions=["מרכז", "איזור השרון"], town_aliases={"כרכור": "פרדס חנה-כרכור"}
    )
    areas = {11: "מרכז", 338: "איזור השרון", 403: "כפר סבא", 51: "תל אביב"}
    street = with_page(a_location=json.dumps("13 ויתקין"))
    [event] = parse_workshop({**ROW, "location": [11, 338, 403]}, street, KINDS, settings, TZ, areas)
    assert (event.venue_name, event.address, event.city) == ("13 ויתקין", "13 ויתקין, כפר סבא", "כפר סבא")
    # Two towns: no guess.
    [event] = parse_workshop({**ROW, "location": [403, 51]}, street, KINDS, settings, TZ, areas)
    assert (event.address, event.city) == ("13 ויתקין", None)
    # A town in the place itself wins over the workshop's areas; an alias gives the town GeoNames knows.
    named = with_page(a_location=json.dumps("ויתקין 13 כפר סבא"))
    [event] = parse_workshop({**ROW, "location": [51]}, named, KINDS, settings, TZ, areas)
    assert (event.address, event.city) == ("ויתקין 13 כפר סבא", None)  # the enricher reads כפר סבא from it
    karkur = with_page(a_location=json.dumps("הרופא, כרכור"))
    [event] = parse_workshop({**ROW, "location": [51]}, karkur, KINDS, settings, TZ, areas)
    assert (event.venue_name, event.city) == ("הרופא, כרכור", "פרדס חנה-כרכור")
