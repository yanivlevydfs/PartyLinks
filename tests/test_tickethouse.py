"""Ticket House parser tests against real Wix Events items (trimmed to the fields read) and a mocked run."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import HttpSettings
from partylinks.scrapers import tickethouse

FIXTURE = Path(__file__).parent / "fixtures" / "tickethouse" / "events.json"
SITE = "https://www.ticket-house.com"
EVENT_URL = SITE + "/event-details/{slug}"


def _items() -> list[dict]:
    return [i["data"] for i in json.loads(FIXTURE.read_text(encoding="utf-8"))["dataItems"]]


def test_tickethouse_parse_event() -> None:
    concert, contact, thessaloniki, festival, match = (tickethouse.parse_event(i, EVENT_URL) for i in _items())
    assert (concert.source_event_id, concert.title, str(concert.url)) == (
        "53ec242b-3451-4a65-a135-348dfa4d2685",
        "איל וולוו בברלין 11.7.2027",
        SITE + "/event-details/yl-wwlww-bbrlyn-11-7-2027",
    )
    assert concert.start_at == datetime(2027, 7, 11, 19, tzinfo=ZoneInfo("Europe/Berlin"))  # the venue's local time
    assert (concert.venue_name, concert.city, concert.country, concert.artists) == (
        "Classic Open Air",
        "ברלין",
        "DE",
        ["איל וולוו"],
    )
    assert (concert.currency, concert.min_price, concert.max_price) == ("ILS", Decimal(379), Decimal(511))
    assert concert.image_url == "https://static.wixstatic.com/media/a54fc8_215b6c9b84c94936a734512470fdbba3~mv2.png"
    assert contact is None and match is None  # registration closed: placeholder, past match
    assert (thessaloniki.city, thessaloniki.country) == ("סלוניקי", "GR")  # address without a country: by time zone
    assert (festival.city, festival.country) == (None, "PT")


def test_tickethouse_sports() -> None:
    sports = ["יובנטוס - פיורנטינה", "ריאל מדריד-מונקו", "גמר ליגת האלופות 2024", "ליגה איטלקית: יובנטוס - מילאן"]
    concerts = ["איל וולוו בברלין 11.7.2027", "ACDC-Munich-9-6-2024", "30 Seconds to Mars בברלין 18.4.2027"]
    assert all(map(tickethouse.is_sport, sports))
    assert not any(map(tickethouse.is_sport, concerts))


def test_tickethouse_scrape_skips_matches() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["dataItems"][-1]["data"]["registrationStatus"] = "OPEN_TICKETS"  # an on-sale match
    queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_api/v1/access-tokens":
            return httpx.Response(200, json={"apps": {"675bbcef-18d8-41f5-800e-131ec9e08762": {"instance": "tok"}}})
        queries.append(json.loads(request.content))
        assert request.headers["Authorization"] == "tok"
        return httpx.Response(200, json=payload)

    cfg = tickethouse.TicketHouseSettings(cron="0 5 * * *", site_url=SITE, event_path="/event-details/{slug}")
    scraper = tickethouse.TicketHouseScraper(
        cfg, HttpSettings(request_delay_seconds=0, max_retries=0), "Asia/Jerusalem"
    )
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[tickethouse.ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    titles = [e.title for e in asyncio.run(run())]
    assert len(titles) == 3 and "יובנטוס - פיורנטינה" not in titles
    assert queries[0]["dataCollectionId"] == "Events/Events" and queries[0]["query"]["filter"]["status"] == "SCHEDULED"
