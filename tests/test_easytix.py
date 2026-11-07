from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.easytix import EasytixSettings, Marketplace, add_tickets, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "easytix"
TZ = "Asia/Jerusalem"
SETTINGS = EasytixSettings(cron="0 0 * * *", skip_accounts=["sarkis-12311"])
COMEDY = Marketplace(domain="comedyclub.co.il", genres=["סטנדאפ"])
ZOO = Marketplace(domain="tickets.jerusalemzoo.org.il", city="ירושלים")


def _events() -> dict[str, dict]:
    return {e["handler"]: e for e in json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))}


def test_events_parse_with_local_time_place_and_genre() -> None:
    events = _events()
    show = parse_event(events["balulu-31-10"], COMEDY, SETTINGS, TZ)
    assert show is not None and show.title == "קובי בלולו במופע סטנדאפ!"
    assert show.start_at is not None and show.start_at.isoformat() == "2026-10-31T21:30:00+02:00" and show.time_known
    assert (show.venue_name, show.address, show.city) == ("קאמל קומדי קלאב", "אלנבי 128א'", "תל-אביב")
    assert str(show.url) == "https://easytix.co.il/event/camel/balulu-31-10"
    assert show.images and show.images[0].startswith("https://") and show.organizer == "קאמל קומדי קלאב"
    assert "stand_up" in (Enricher(Settings().enrichment, TZ).enrich(show) or show).genres
    # The day comes from start_date, the time from start_hour; escaped quotes are undone.
    weekend = parse_event(events["camel-20-11"], COMEDY, SETTINGS, TZ)
    assert weekend is not None and '\\"' not in weekend.title and weekend.start_at is not None
    assert weekend.start_at.isoformat() == "2026-11-20T22:00:00+02:00"
    aquarium = parse_event(events["Wellness"], ZOO, SETTINGS, TZ)
    assert aquarium is not None and (aquarium.venue_name, aquarium.city) == ("אקווריום ישראל", "ירושלים")
    assert aquarium.start_at is not None and aquarium.start_at.isoformat() == "2026-11-03T17:00:00+02:00"


def test_not_events_are_skipped() -> None:
    events = _events()
    assert parse_event(events["soundhealing"], None, SETTINGS, TZ) is None  # eight months: a pass
    assert parse_event(events["zoo"], ZOO, SETTINGS, TZ) is None  # admission ticket until 2030
    assert parse_event(events["Onlife"], None, SETTINGS, TZ) is None  # disabled
    assert parse_event(events["Yinyoga_12-07-26"], None, SETTINGS, TZ) is None  # members only


def test_tickets_give_the_paid_price_range() -> None:
    show = parse_event(_events()["roey-07-01"], COMEDY, SETTINGS, TZ)
    assert show is not None and (show.venue_name, show.city) == ("תיאטרון גבעתיים", None)
    tickets = json.loads((FIXTURES / "tickets.json").read_text(encoding="utf-8"))
    priced = add_tickets(show, tickets, datetime.fromisoformat("2026-10-30T12:00:00+02:00"))
    assert (priced.min_price, priced.max_price, priced.is_sold_out) == (89, 119, False)
    assert priced.tickets and not any(t.is_free for t in priced.tickets)  # the wheelchair seat is not free entry


def test_linked_pages_on_the_box_offices_only() -> None:
    from partylinks.scrapers.easytix import linked_pages

    urls = [
        "https://easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN?utm=175cf306&fbclid=x",
        "https://www.easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN",
        "https://comedyclub.co.il/event/camel/balulu-31-10/",
        "https://easytix.co.il/BLACKOUT",
        "https://goout.net/event/BLACKOUT/x",
    ]
    hosts = {"easytix.co.il", "comedyclub.co.il"}
    assert linked_pages(urls, hosts) == [("BLACKOUT", "BLACKOUTHALLOWEEN"), ("camel", "balulu-31-10")]


def test_an_unlisted_event_another_source_links_to_is_read() -> None:
    import asyncio
    from datetime import UTC, timedelta

    import httpx

    from partylinks.config import HttpSettings
    from partylinks.scrapers.easytix import EasytixScraper

    day = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
    account = {"_id": "69f31312fe790994a454aba2", "handler": "BLACKOUT", "name": "BLACKOUT by Shirley"}
    event = {
        "_id": "6ae9e56663fe1e1943d8b207", "handler": "BLACKOUTHALLOWEEN", "name": "VELVET BLACKOUT",
        "account": account["_id"], "marketplace": "m1", "is_enabled": True, "isPublic": False,
        "start_date": f"{day}T12:00:00.000Z", "start_hour": f"{day}T21:30:00.000Z", "location": "Gagarin Club",
        "type": "techno-party",
    }  # fmt: skip

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("getMarketplaceIdByDomainRegex"):
            return httpx.Response(200, json="m1")
        if path.startswith("/event/public-events/"):
            return httpx.Response(200, json=[])
        if path == "/user/account/getByHandler/BLACKOUT":
            return httpx.Response(200, json=account)
        if path == f"/event/getEventByHandler/{account['_id']}/BLACKOUTHALLOWEEN":
            return httpx.Response(200, json=event)
        if path.startswith("/event/ticket/getTickets/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    settings = EasytixSettings(cron="0 * * * *", proxied=False, marketplaces=[Marketplace(domain="easytix.co.il")])
    scraper = EasytixScraper(settings, HttpSettings(max_retries=0), TZ)
    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]
    scraper.linked_urls = ["https://easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN?utm=175cf306"]

    async def run() -> list:
        async with scraper:
            return [e async for e in scraper.scrape()]

    [found] = asyncio.run(run())
    assert found.source_event_id == event["_id"] and found.title == "VELVET BLACKOUT"
    assert (
        str(found.url) == "https://easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN"
        and found.venue_name == "Gagarin Club"
    )
    assert found.organizer == "BLACKOUT by Shirley"
