from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import zone
from partylinks.scrapers.easytix import EasytixSettings, Marketplace, add_tickets, parse_event, soonest_first

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


def test_a_stored_event_whose_handler_changed_gets_its_new_link() -> None:
    """BLACKOUT turned BLACKOUTHALLOWEEN into VELVET BLACKOUT, then renamed its handler VELVETBLACKOUT
    (Nov 2026): the stored link sent buyers to the old Halloween page. The stored event is read by id."""
    import asyncio
    from datetime import UTC, timedelta

    import httpx

    from partylinks.config import HttpSettings
    from partylinks.scrapers.easytix import EasytixScraper

    day = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
    account = {"_id": "69f31312fe790994a454aba2", "handler": "BLACKOUT", "name": "BLACKOUT by Shirley"}
    event = {
        "_id": "6ae9e56663fe1e1943d8b207", "handler": "VELVETBLACKOUT", "name": "VELVET BLACKOUT",
        "account": account["_id"], "marketplace": "m1", "is_enabled": True, "isPublic": False,
        "start_date": f"{day}T12:00:00.000Z", "start_hour": f"{day}T21:30:00.000Z", "location": "Gagarin Club",
    }  # fmt: skip
    old = "https://easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN"
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path.endswith("getMarketplaceIdByDomainRegex"):
            return httpx.Response(200, json="m1")
        if path.startswith("/event/public-events/"):
            return httpx.Response(200, json=[])
        if path == "/user/account/getByHandler/BLACKOUT":
            return httpx.Response(200, json=account)
        if path == f"/event/getPublicEventById/{event['_id']}":
            return httpx.Response(200, json=event)
        if path.startswith("/event/getEventByHandler/"):
            return httpx.Response(400, json={"error": "event_not_found_handler"})
        if path.startswith("/event/ticket/getTickets/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    settings = EasytixSettings(cron="0 * * * *", proxied=False, marketplaces=[Marketplace(domain="easytix.co.il")])
    scraper = EasytixScraper(settings, HttpSettings(max_retries=0), TZ)
    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]
    scraper.own_pages = {event["_id"]: old}
    scraper.linked_urls = [old + "?utm=29da6376"]  # another source still links the old page

    async def run() -> list:
        async with scraper:
            return [e async for e in scraper.scrape()]

    [found] = asyncio.run(run())
    assert str(found.url) == "https://easytix.co.il/event/BLACKOUT/VELVETBLACKOUT"
    assert found.title == "VELVET BLACKOUT" and found.organizer == "BLACKOUT by Shirley"
    assert not any("getEventByHandler" in p for p in asked)  # the old address is not asked again
    assert scraper.failures == []


def test_a_link_to_a_handler_no_event_has_is_not_a_failure() -> None:
    import asyncio

    import httpx

    from partylinks.config import HttpSettings
    from partylinks.scrapers.easytix import EasytixScraper

    account = {"_id": "a1", "handler": "BLACKOUT", "name": "BLACKOUT by Shirley"}

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("getMarketplaceIdByDomainRegex"):
            return httpx.Response(200, json="m1")
        if path.startswith("/event/public-events/"):
            return httpx.Response(200, json=[])
        if path == "/user/account/getByHandler/BLACKOUT":
            return httpx.Response(200, json=account)
        if path.startswith("/event/getEventByHandler/"):
            return httpx.Response(400, json={"error": "event_not_found_handler"})
        return httpx.Response(404)

    settings = EasytixSettings(cron="0 * * * *", proxied=False, marketplaces=[Marketplace(domain="easytix.co.il")])
    scraper = EasytixScraper(settings, HttpSettings(max_retries=0), TZ)
    scraper._new_client = lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(answer))  # type: ignore[method-assign]
    scraper.linked_urls = ["https://easytix.co.il/event/BLACKOUT/BLACKOUTHALLOWEEN"]

    async def run() -> list:
        async with scraper:
            return [e async for e in scraper.scrape()]

    assert asyncio.run(run()) == [] and scraper.failures == []


def test_tickets_are_read_soonest_first() -> None:
    def show(i: str, day: int | None) -> ScrapedEvent:
        start = datetime(2026, 11, day, 21, tzinfo=zone(TZ)) if day else None
        return ScrapedEvent.model_validate(
            {"source": "easytix", "source_event_id": i, "url": "https://easytix.co.il/", "title": i, "start_at": start}
        )

    ordered = soonest_first([show("far", 28), show("undated", None), show("near", 19)])
    assert [e.source_event_id for e in ordered] == ["near", "far", "undated"]


def _slots(*days: tuple[str, str, str], weekly: bool = False) -> dict:
    week = {
        d: {"enabled": weekly and d == "monday", "startTime": "0900", "endTime": "1700"} for d in ("monday", "friday")
    }
    overrides = [{"date": d, "schedule": {"enabled": True, "startTime": a, "endTime": b}} for d, a, b in days]
    return {"enabled": True, "daySchedules": week, "dateOverrides": overrides}


def _penguin(slots: dict) -> dict:
    return {
        "_id": "6a5ce559fb2e7ae193e4fce3", "name": "אנשי הפינגווין ALIVE", "handler": "PENGUIN", "type": "music",
        "account": {"name": "אנשי הפינגווין", "handler": "PENGUIN", "_id": "6a4fea6c67abb6fb75fbca57"},
        "is_enabled": True, "isPublic": True, "location": "מועדון Lost & Found",
        "start_date": "2026-12-03T12:00:00.000Z", "end_date": "2027-01-09T12:00:00.000Z",
        "start_hour": "2026-12-03T19:00:00.000Z", "end_hour": "2027-01-09T00:00:00.000Z", "timeSlotConfig": slots,
    }  # fmt: skip


def test_a_run_of_dated_shows_is_one_event_per_date() -> None:
    from partylinks.scrapers.easytix import parse_events

    slots = _slots(("2026-12-07", "1700", "2200"), ("2026-12-03", "2100", "0100"), ("2027-02-01", "1700", "2200"))
    slots["dateOverrides"].append({"date": "2026-12-08", "schedule": {"enabled": False, "startTime": "1700"}})
    # Five weeks from the first day to the last: too long as one event, so it was dropped (Nov 2026).
    assert parse_event(_penguin(slots), None, SETTINGS, TZ) is None
    opening, evening = parse_events(_penguin(slots), None, SETTINGS, TZ)  # Feb 1 is after end_date, Dec 8 closed
    assert opening.source_event_id == "6a5ce559fb2e7ae193e4fce3"  # start_date's day keeps the event's id
    assert opening.start_at is not None and opening.end_at is not None
    assert (opening.start_at.isoformat(), opening.end_at.isoformat()) == (
        "2026-12-03T21:00:00+02:00",
        "2026-12-04T01:00:00+02:00",
    )
    assert (
        evening.source_event_id == "6a5ce559fb2e7ae193e4fce3:2026-12-07"
        and evening.extra["event_id"] == opening.source_event_id
    )
    assert evening.start_at is not None and evening.start_at.isoformat() == "2026-12-07T17:00:00+02:00"
    assert evening.end_at is not None and evening.end_at.isoformat() == "2026-12-07T22:00:00+02:00"
    assert (
        str(evening.url) == "https://easytix.co.il/event/PENGUIN/PENGUIN"
        and evening.venue_name == "מועדון Lost & Found"
    )
    # One short arrival slot (the aquarium's sleepover tour, 17:30-18:00) is when it starts, not its end.
    [night] = parse_events(_penguin(_slots(("2026-12-10", "1730", "1800"))), None, SETTINGS, TZ)
    assert night.start_at is not None and night.start_at.hour == 17 and night.end_at is None


def test_a_weekly_calendar_stays_one_event() -> None:
    from partylinks.scrapers.easytix import parse_events, slot_days

    weekly = _penguin(_slots(("2026-12-07", "1700", "2200"), weekly=True))  # an admission ticket, a weekly class
    assert slot_days(weekly, TZ) == [] and parse_events(weekly, None, SETTINGS, TZ) == []
    short = {**weekly, "end_date": "2026-12-20T12:00:00.000Z", "end_hour": ""}
    [one] = parse_events(short, None, SETTINGS, TZ)
    assert one.source_event_id == "6a5ce559fb2e7ae193e4fce3" and one.start_at is not None
    assert one.start_at.isoformat() == "2026-12-03T21:00:00+02:00"


def test_a_venue_named_alone_gets_its_town() -> None:
    settings = SETTINGS.model_copy(update={"venue_cities": {"lost & found": "תל אביב-יפו"}})
    opening = {**_penguin({}), "end_date": None, "end_hour": None}  # "מועדון Lost & Found": no town in it
    show = parse_event(opening, None, settings, TZ)
    assert show is not None and (show.venue_name, show.city) == ("מועדון Lost & Found", "תל אביב-יפו")
    plain = parse_event(opening, None, SETTINGS, TZ)
    assert plain is not None and plain.city is None
