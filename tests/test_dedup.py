"""Duplicate listings collapse into one primary event; different events never merge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upsert_events
from partylinks.dedup import Deduplicator
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent, TicketTier

START = datetime.now(UTC).replace(hour=19, minute=30, second=0, microsecond=0) + timedelta(days=3)
MOON = (32.1002, 34.7745)


def listing(source: str, eid: str, title: str, start: datetime = START, where: tuple[float, float] | None = MOON, **extra: Any) -> ScrapedEvent:
    fields: dict[str, Any] = {
        "source": source, "source_event_id": eid, "url": f"https://{source}.example/{eid}", "title": title,
        "start_at": start, "address": "Tel Aviv-Yafo, Israel", "latitude": where[0] if where else None,
        "longitude": where[1] if where else None, "tickets": [TicketTier(name="Entry", price=Decimal(80))],
    }  # fmt: skip
    return ScrapedEvent(**(fields | extra))


def grouped(tmp_path: Path, events: list[ScrapedEvent]) -> tuple[Settings, dict[str, tuple[str | None, bool]]]:
    settings = Settings(scheduler={"enabled": False})
    db = Database(settings.database.url)
    db.create_schema()
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        upsert_events(session, [e for e in map(enricher.enrich, events) if e])
    with db.session() as session:
        Deduplicator(settings.dedup).regroup(session)
    with db.session() as session:
        return settings, {r.id: (r.group_id, r.is_primary) for r in session.query(EventRow)}


def test_promoter_copies_and_cross_platform_listings_merge(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM | 22.10"),
        listing("goout", "b", "22.10 THURSDAY MOON | MAINSTREAM |"),
        listing("eventer", "c", "THURSDAY MOON | MAINSTREAM | 22.10 🌙", start=START + timedelta(minutes=30)),
        listing("tickchak", "d", "THURSDAY MOON", where=None, extra={"purchase_url": "https://goout.example/a"}),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1
    assert [eid for eid, (_, primary) in groups.items() if primary] == ["goout:a"]  # source priority, not tickchak


def test_different_events_stay_separate(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM"),
        listing("goout", "b", "THURSDAY 22.10 | PANDA TLV"),  # same place and time, different party
        listing("goout", "c", "THURSDAY MOON | MAINSTREAM", start=START + timedelta(hours=3)),  # later show
        listing("goout", "d", "THURSDAY MOON | MAINSTREAM", where=(32.05, 34.76)),  # another venue
        listing("goout", "e", "THURSDAY MOON | MAINSTREAM", start=START + timedelta(days=7)),  # next week
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 5 and all(primary for _, primary in groups.values())


def test_link_to_one_query_routed_page_merges_only_that_page(tmp_path: Path) -> None:
    page = "https://www.tel-aviv.gov.il/Pages/MainItemPage.aspx?WebID=w&ListID=l&ItemID={}"
    _, groups = grouped(tmp_path, [
        listing("telaviv", "1", "קורס ביומימקרי", url=page.format(1)),
        listing("telaviv", "2", "סדנת מנגה", url=page.format(2)),
        listing("ogo", "x", "Manga workshop", extra={"purchase_url": page.format(2) + "&utm_source=ogo"}),
    ])  # fmt: skip
    assert groups["telaviv:2"][0] == groups["ogo:x"][0] != groups["telaviv:1"][0]


def test_hourly_sessions_never_chain_into_one_event(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("smarticket", str(n), "סדנת חוסן באמצעות בעלי חיים", start=START + timedelta(hours=n)) for n in range(3)
    ] + [listing("goout", "a", "סדנת חוסן באמצעות בעלי חיים", start=START + timedelta(minutes=30))])  # fmt: skip
    assert len({g for g, _ in groups.values()}) >= 3
    assert len({groups[f"smarticket:{n}"][0] for n in range(3)}) == 3


def test_link_to_a_page_many_listings_share_merges_nothing(tmp_path: Path) -> None:
    central = "https://www.mevalim.co.il/central/"
    _, groups = grouped(tmp_path, [
        listing("mevalim", "1", "הברווז", url=central),
        listing("mevalim", "2", "קומדיה אחרת", url=central, start=START + timedelta(hours=2)),
        listing("ogo", "x", "Something else", extra={"purchase_url": central}),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 3


def test_same_title_and_hour_in_another_city_stays_apart(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("telaviv", "1", "שעת סיפור", where=None, city="תל אביב", address=None),
        listing("haifamuni", "2", "שעת סיפור", where=None, city="חיפה", address=None),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 2


def test_kids_show_and_lecture_of_a_similar_name_stay_apart(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("smarticket", "1", "מהפכה הביטלס עשו", source_genres=["kids"]),  # 0.89 alike
        listing("arenas", "2", "מהפכה הביטלס עשו לנו", source_genres=["lectures"]),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 2


def test_no_place_at_all_never_merges_across_sites(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("telaviv", "1", "יוגה", where=None, address=None),
        listing("haifamuni", "2", "יוגה", where=None, address=None),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 2


def test_matches_never_chain_past_the_start_gap(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("eventer", "a", "THURSDAY MOON MAINSTREAM"),
        listing("tickchak", "b", "THURSDAY MOON MAINSTREAM", start=START + timedelta(minutes=50)),
        listing("barby", "c", "THURSDAY MOON MAINSTREAM", start=START + timedelta(minutes=100)),
    ])  # fmt: skip
    assert groups["eventer:a"][0] != groups["barby:c"][0]


def test_same_city_alone_does_not_merge_a_generic_title(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("telaviv", "1", "שעת סיפור", where=None, city="תל אביב", address=None),
        listing("ogo", "2", "שעת סיפור", city="תל אביב", where=(32.08, 34.78), address=None),
        listing("ogo", "3", "סדנת מנגה קומיקס יפני", city="תל אביב", where=(32.08, 34.78), address=None),
        listing("telaviv", "4", "סדנת מנגה - קומיקס יפני", where=None, city="תל אביב", address=None),
    ])  # fmt: skip
    assert groups["telaviv:1"][0] != groups["ogo:2"][0]  # generic: could be any library
    assert groups["telaviv:4"][0] == groups["ogo:3"][0]


def test_promoter_pages_hours_apart_never_chain(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", str(h), "STAND UP X", start=START + timedelta(hours=h)) for h in range(3)
    ])  # fmt: skip
    assert groups["goout:0"][0] != groups["goout:2"][0]


def test_promoter_pages_of_one_party_keep_their_entry_times_together(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("goout", str(m), "PETTRA x HELLO VERA FRIDAY OPEN AIR", start=START + timedelta(minutes=m),
                source_genres=["techno"]) for m in (0, 30, 60, 90)
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1


def test_sold_out_copy_still_prices_the_event_when_no_other_copy_has_a_price(tmp_path: Path) -> None:
    settings, _ = grouped(tmp_path, [
        listing("smarticket", "a", "סדנת חוסן באמצעות בעלי חיים", is_sold_out=True, min_price=Decimal(40),
                tickets=[TicketTier(name="Entry", price=Decimal(40), is_sold_out=True)]),
        listing("ogo", "b", "סדנת חוסן באמצעות בעלי חיים", tickets=[]),
    ])  # fmt: skip
    with Database(settings.database.url).session() as session:
        rows = {r.id: r for r in session.query(EventRow)}
    assert rows["ogo:b"].group_id == rows["smarticket:a"].group_id
    assert rows["ogo:b"].group_min_price == 40 and rows["ogo:b"].group_entry_type == "paid"


def test_one_party_on_many_sites_with_other_entry_times_is_one_event(tmp_path: Path) -> None:
    moonchild, port = (32.1001, 34.7745), (32.0962, 34.7732)  # ~440 m: the club and the port's pin
    title = "PETTRA x HELLO VERA • FRIDAY OPEN AIR"
    _, groups = grouped(tmp_path, [
        listing("beatradar", "a", title, where=moonchild, source_genres=["techno"]),
        listing("zygo", "b", title, start=START + timedelta(minutes=30), where=port, source_genres=["techno"]),
        listing("popit", "c", title, start=START + timedelta(minutes=60), where=port, source_genres=["techno"]),
        listing("happytlv", "d", title, start=START + timedelta(minutes=90), where=moonchild, source_genres=["techno"]),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1


def test_closest_session_wins(tmp_path: Path) -> None:
    title = "ARTIST LIVE CONCERT TOUR"
    _, groups = grouped(tmp_path, [
        listing("barby", "1", title, source_genres=["rock_indie"]),
        listing("barby", "2", title, start=START + timedelta(minutes=80), source_genres=["rock_indie"]),
        listing("tickchak", "x", title, start=START + timedelta(minutes=75), source_genres=["rock_indie"]),
    ])  # fmt: skip
    assert groups["tickchak:x"][0] == groups["barby:2"][0] != groups["barby:1"][0]


def test_one_sites_pages_under_other_titles_at_one_hour_stay_apart(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("smarticket", "1", 'גילאי שנה עד שלוש - ר"געים משחקים'),
        listing("smarticket", "2", 'לידה עד גיל שנה - ר"געים משחקים'),
        listing("secrettelaviv", "3", "Idan Raichel and the Israel Philharmonic"),
        listing("secrettelaviv", "4", "Idan Raichel and the Israel Philharmonic"),  # the site's own copy
    ])  # fmt: skip
    assert groups["smarticket:1"][0] != groups["smarticket:2"][0]
    assert groups["secrettelaviv:3"][0] == groups["secrettelaviv:4"][0]


def test_numbered_variants_stay_apart(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("telaviv", "1", "חוג ציור לגילאי 4-6"),
        listing("ogo", "2", "חוג ציור לגילאי 7-9"),
        listing("ogo", "3", "חוג ציור לגילאי 4-6 | 22.10"),
    ])  # fmt: skip
    assert groups["telaviv:1"][0] != groups["ogo:2"][0]
    assert groups["telaviv:1"][0] == groups["ogo:3"][0]


def test_one_sites_listings_linking_one_page_do_not_merge_through_it(tmp_path: Path) -> None:
    venue = "https://venue.example/tickets"
    _, groups = grouped(tmp_path, [
        listing("venue", "z", "Jazz in the park", url=venue),
        listing("ogo", "x", "Jazz in the park", extra={"purchase_url": venue}),
        listing("ogo", "y", "Kids theatre", extra={"purchase_url": venue}),
    ])  # fmt: skip
    assert groups["ogo:x"][0] != groups["ogo:y"][0]


def test_sold_out_copy_never_fronts_or_prices_the_event(tmp_path: Path) -> None:
    settings, _ = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON", is_sold_out=True, min_price=Decimal(80),
                tickets=[TicketTier(name="Entry", price=Decimal(80), is_sold_out=True)]),
        listing("eventer", "b", "THURSDAY MOON", tickets=[TicketTier(name="Entry", price=Decimal(120))]),
    ])  # fmt: skip
    with Database(settings.database.url).session() as session:
        rows = {r.id: r for r in session.query(EventRow)}
    assert rows["eventer:b"].is_primary and not rows["goout:a"].is_primary
    assert rows["goout:a"].group_min_price == 120


def test_api_returns_one_event_with_alternatives(tmp_path: Path) -> None:
    settings, _ = grouped(tmp_path, [
        listing("goout", "a", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(90))]),
        listing("selector", "b", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(70))]),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events").json()
        assert page["total"] == 1 and page["items"][0]["listings"] == 2
        detail = client.get(f"/events/{page['items'][0]['id']}").json()
        assert [(a["source"], a["min_price"]) for a in detail["alternatives"]] == [("selector", 70.0)]
        assert client.get("/events", params={"include_duplicates": True}).json()["total"] == 2


def test_unlisted_copy_never_hides_the_listed_one(tmp_path: Path) -> None:
    # goout outranks selector as primary; once goout stops listing it, selector's copy must still show.
    settings, _ = grouped(tmp_path, [listing("goout", "a", "WHITE THURSDAY"), listing("selector", "b", "WHITE THURSDAY")])
    db = Database(settings.database.url)
    with db.session() as session:
        session.query(EventRow).filter(EventRow.id == "goout:a").update({"is_listed": False})
    with db.session() as session:
        Deduplicator(settings.dedup).regroup(session)
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/events").json()
        assert page["total"] == 1 and page["items"][0]["source"] == "selector"


def test_free_or_cheaper_listing_on_another_source_is_never_hidden(tmp_path: Path) -> None:
    settings, groups = grouped(tmp_path, [
        listing("goout", "a", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[TicketTier(name="Entry", price=Decimal(90))]),
        listing("selector", "b", "WHITE THURSDAY - MAOR BUZAGLO", tickets=[
            TicketTier(name="רשומים חינם עד 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(70)),
        ]),
    ])  # fmt: skip
    assert groups["goout:a"][1] is True  # go-out stays the primary listing (source priority)...
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        free = client.get("/events", params={"free_entry": True}).json()  # ...but the free deal is found
        assert free["total"] == 1
        event = free["items"][0]
        assert (event["id"], event["group_has_free_entry"], event["group_entry_type"]) == (
            "goout:a", True, "free_and_paid",
        )  # fmt: skip
        assert (event["group_min_price"], event["best_deal"]["source"]) == (0.0, "selector")
        assert client.get("/events", params={"max_price": 50}).json()["total"] == 1
        detail = client.get("/events/goout:a").json()
        assert [t["kind"] for t in detail["alternatives"][0]["tickets"]] == ["free", "paid"]


def test_area_level_pin_merges_only_on_same_title_and_time(tmp_path: Path) -> None:
    port = (32.0962, 34.7732)  # an aggregator's "Tel Aviv Port" pin, ~460 m from the club
    _, groups = grouped(tmp_path, [
        listing("goout", "a", "FRIDAY MAINSTREAM | 06.11"),
        listing("popit", "b", "FRIDAY MAINSTREAM | 06.11", where=port),
        listing("popit", "c", "SATURDAY TECHNO NIGHT", where=port),  # other party, same coarse pin
        listing("popit", "d", "FRIDAY MAINSTREAM | 06.11", start=START + timedelta(hours=2), where=port),
    ])  # fmt: skip
    assert groups["goout:a"][0] == groups["popit:b"][0]
    assert groups["popit:c"][0] != groups["goout:a"][0]
    assert groups["popit:d"][0] != groups["goout:a"][0]  # same title, different time


def test_happy_hours_of_neighbouring_bars_stay_apart(tmp_path: Path) -> None:
    bar = {"extra": {"happy_hour": True}, "source_genres": ["happy hour"]}
    _, groups = grouped(tmp_path, [
        listing("happytlv", "hh-a", "האפי האוור באהובה", where=(32.0800, 34.7740), venue_name="אהובה", **bar),
        listing("happytlv", "hh-b", "האפי האוור באורבנו", where=(32.0801, 34.7741), venue_name="אורבנו", **bar),
    ])  # fmt: skip
    assert groups["happytlv:hh-a"][0] != groups["happytlv:hh-b"][0]


def test_second_venue_and_group_pass_rewrites_nothing(tmp_path: Path) -> None:
    from partylinks.venues import resolve_venues, venue_updates

    settings, _ = grouped(tmp_path, [
        listing("a", "1", "Techno Night", venue_name="Moon Child"),
        listing("b", "2", "TECHNO NIGHT", venue_name="Moonchild TLV"),
        listing("c", "3", "Jazz", venue_name="Moon Child", where=None),
    ])  # fmt: skip
    db = Database(settings.database.url)
    with db.session() as session:
        resolve_venues(session)
        Deduplicator(settings.dedup).regroup(session)
    with db.session(write=False) as session:  # nothing left to change: no rows held under the write lock
        assert venue_updates(session) == []
        assert Deduplicator(settings.dedup).plan(session)[1] == []


ZOA = (32.074601, 34.7845)


def test_box_office_filler_and_extra_words_merge_at_one_hour_and_place(tmp_path: Path) -> None:
    later = START + timedelta(days=1)
    _, groups = grouped(tmp_path, [
        listing("tickchak", "1", "דרור קרן", where=ZOA, source_genres=["stand_up"]),
        listing("zoatlv", "2", "דרור קרן", where=ZOA),
        listing("mevalim", "3", "דרור קרן במופע סטנדאפ", where=ZOA, source_genres=["stand_up"]),
        listing("kupatbravo", "4", "שומרי הדרקונים - חנוכה 2026", start=later, source_genres=["kids"]),
        listing("smarticket", "5", "שומרי הדרקונים - מחזמר הרפתקאות - חנוכה 2026", start=later, source_genres=["kids"]),
        listing("kupatbravo", "6", "סינדרלה – מבית הבלט הישראלי", start=later + timedelta(hours=3)),
        listing("smarticket", "7", "סינדרלה - מבית הבלט הישראלי חנוכה 2026", start=later + timedelta(hours=3)),
    ])  # fmt: skip
    assert groups["tickchak:1"][0] == groups["zoatlv:2"][0] == groups["mevalim:3"][0]
    assert groups["kupatbravo:4"][0] == groups["smarticket:5"][0]
    assert groups["kupatbravo:6"][0] == groups["smarticket:7"][0] != groups["kupatbravo:4"][0]


def test_extra_words_never_merge_other_shows_hours_or_places(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("tickchak", "1", "דרור קרן", where=ZOA),
        listing("mevalim", "2", "שחר חסון", where=ZOA),  # another hall, same hour
        listing("goout", "3", "Techno night", where=ZOA),
        listing("eventer", "4", "Techno brunch", where=ZOA),  # one shared word is no name
        listing("smarticket", "5", "שומרי הדרקונים", start=START + timedelta(minutes=45)),
        listing("kupatbravo", "6", "שומרי הדרקונים - מחזמר הרפתקאות", start=START + timedelta(minutes=45), where=(32.0962, 34.7732)),
        listing("leaan", "7", "דרור קרן ואורחים", where=ZOA, start=START + timedelta(minutes=40)),
    ])  # fmt: skip
    assert len({groups[f"{s}:{n}"][0] for s, n in [("tickchak", 1), ("mevalim", 2), ("goout", 3), ("eventer", 4)]}) == 4
    assert groups["smarticket:5"][0] != groups["kupatbravo:6"][0]  # 460 m apart: subset needs one spot
    assert groups["leaan:7"][0] != groups["tickchak:1"][0]  # 40 min apart: subset needs one hour


def test_date_only_listing_joins_its_show_by_name_and_place(tmp_path: Path) -> None:
    midnight = START.replace(hour=0, minute=0)
    _, groups = grouped(tmp_path, [
        listing("tickchak", "1", "דרור קרן", where=ZOA),
        listing("mevalim", "2", "דרור קרן במופע", where=ZOA, start=midnight, time_known=False),
        listing("goout", "3", "שחר חסון", where=ZOA, start=midnight, time_known=False),
    ])  # fmt: skip
    assert groups["tickchak:1"][0] == groups["mevalim:2"][0] != groups["goout:3"][0]
