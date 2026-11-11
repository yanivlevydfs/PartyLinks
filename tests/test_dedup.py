"""Duplicate listings collapse into one primary event; different events never merge."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


def test_one_hall_by_name_merges_whatever_the_pins_say(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("tickchak", "a", "אורי חזקיה", venue_name="תרבותא מונגש"),
        listing("eventer", "b", "אורי חזקיה", venue_name="תרבותא מונגש", where=(32.25, 34.95)),  # pin ~20 km off
        listing("pres", "c", "אורי חזקיה", venue_name="תרבותא מונגש", where=(32.25, 34.95), start=START + timedelta(hours=2)),
    ])  # fmt: skip
    assert groups["tickchak:a"][0] == groups["eventer:b"][0] != groups["pres:c"][0]  # the later hour is another show


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


def test_guides_typo_price_never_prices_the_event(tmp_path: Path) -> None:
    # Makore listed Monica Sex at Reading 3 for "13 ₪" (no tiers); Muzi sells it from 120.
    settings, _ = grouped(tmp_path, [
        listing("muzi", "a", "מוניקה סקס", tickets=[TicketTier(name="כרטיס", price=Decimal(120)),
                                                   TicketTier(name="כרטיס", price=Decimal(145))]),
        listing("makore", "b", "מוניקה סקס", min_price=Decimal(13), tickets=[]),
        listing("makore", "c", "Free jam", min_price=Decimal(0), tickets=[]),
        listing("muzi", "d", "Free jam", tickets=[TicketTier(name="Entry", price=Decimal(50))]),
    ])  # fmt: skip
    with Database(settings.database.url).session() as session:
        rows = {r.id: r for r in session.query(EventRow)}
    assert rows["makore:b"].group_id == rows["muzi:a"].group_id
    assert rows["makore:b"].group_min_price == 120 and rows["makore:b"].best_listing_id == "muzi:a"
    assert rows["muzi:d"].group_min_price == 0  # free stays free


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
        assert detail["alternatives"][0]["resale"] is False
        assert client.get("/events", params={"include_duplicates": True}).json()["total"] == 2


def test_resale_alternative_is_flagged(tmp_path: Path) -> None:
    # The event page shows second-hand marketplaces (viagogo, SwapTix) apart from the box offices.
    settings, _ = grouped(tmp_path, [
        listing("goout", "a", "WHITE THURSDAY - MAOR BUZAGLO"),
        listing("viagogo", "b", "WHITE THURSDAY - MAOR BUZAGLO", extra={"resale": True}),
        listing("viagogo", "c", "WHITE THURSDAY - MAOR BUZAGLO", extra={"resale": True, "has_listings": False}),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        primary = next(e for e in client.get("/events", params={"include_duplicates": True}).json()["items"] if e["source"] == "goout")
        detail = client.get(f"/events/{primary['id']}").json()
        assert sorted((a["resale"], a["is_sold_out"]) for a in detail["alternatives"]) == [(True, False), (True, True)]  # no seller: sold out


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


def test_a_listing_takes_what_it_lacks_from_the_other_stores_of_its_event(tmp_path: Path) -> None:
    from partylinks import group_fill

    settings, groups = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM", artists=["DOR HADAD"], min_age=21),
        listing("tickchak", "d", "THURSDAY MOON", where=None, extra={"purchase_url": "https://goout.example/a"}),
    ])  # fmt: skip
    assert groups["goout:a"][0] == groups["tickchak:d"][0]
    db = Database(settings.database.url)
    with db.session() as session:
        fills = group_fill.plan(session)
    assert [f["id"] for f in fills] == ["tickchak:d"]
    filled = fills[0]
    assert (filled["latitude"], filled["longitude"]) == MOON
    assert filled["lineup"] == ["DOR HADAD"] and filled["min_age"] == 21


def test_stored_lineups_are_cleaned_by_the_current_name_rule(tmp_path: Path) -> None:
    from partylinks import group_fill

    settings, _ = grouped(tmp_path, [listing("shotgun", "a", "NUIT NOIRE")])
    db = Database(settings.database.url)
    with db.session() as session:
        session.get_one(EventRow, "shotgun:a").lineup = ["01] DJ IAGO", "00h00 — 06h30", "______ BLACK", "DJ IAGO", "ANSTY"]
    enricher = Enricher(settings.enrichment, settings.timezone)
    with db.session() as session:
        fills = group_fill.plan(session, enricher.clean_name)
    assert [f["lineup"] for f in fills] == [["DJ IAGO", "ANSTY"]]


def test_one_sites_hour_just_after_midnight_joins_the_others_and_the_agreed_hour_fronts(tmp_path: Path) -> None:
    tz = ZoneInfo("Asia/Jerusalem")
    day = datetime.now(tz).date() + timedelta(days=5)
    night, typo = datetime.combine(day, time(23, 50), tz), datetime.combine(day, time(0, 50), tz)
    genres = {"source_genres": ["psytrance"]}
    _, groups = grouped(tmp_path, [
        listing("goout", "typo", "מסיבת טבע - OKAVANGO DELTA", start=typo, where=None, **genres),
        listing("zygo", "a", "OKAVANGO DELTA", start=night, where=None, **genres),
        listing("selector", "b", "OKAVANGO DELTA", start=night, where=None, **genres),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1
    assert not groups["goout:typo"][1]  # goout outranks them, but only its hour is the odd one out


def test_a_midnight_guess_stays_apart_from_a_longer_name_abroad(tmp_path: Path) -> None:
    tz = ZoneInfo("Asia/Jerusalem")
    day = datetime.now(tz).date() + timedelta(days=5)
    genres = {"source_genres": ["techno"]}
    _, groups = grouped(tmp_path, [
        listing("posh", "a", "Pub Crawl in Malta Paceville", start=datetime.combine(day, time(0, 30), tz), where=None, country="MT", **genres),
        listing("zygo", "b", "Pub Crawl", start=datetime.combine(day, time(23, 30), tz), where=None, **genres),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 2


def test_a_page_with_one_sites_wrong_hour_shows_the_agreed_listing(tmp_path: Path) -> None:
    tz = ZoneInfo("Asia/Jerusalem")
    day = datetime.now(tz).date() + timedelta(days=5)
    night, typo = datetime.combine(day, time(23, 50), tz), datetime.combine(day, time(0, 50), tz)
    genres = {"source_genres": ["psytrance"]}
    settings, _ = grouped(tmp_path, [
        listing("goout", "typo", "מסיבת טבע - OKAVANGO DELTA", start=typo, where=None, **genres),
        listing("zygo", "a", "OKAVANGO DELTA", start=night, where=None, **genres),
        listing("selector", "b", "OKAVANGO DELTA", start=night, where=None, **genres),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert client.get("/events/goout:typo").json()["start_at"][11:16] == "21:50"  # 23:50 in Israel, stored in UTC
        assert "00:50" not in client.get("/events/goout:typo").json()["start_at"]


def test_guides_linking_one_box_office_page_under_other_names_merge(tmp_path: Path) -> None:
    _, groups = grouped(tmp_path, [
        listing("smarticket", "shablul:4833", "ספיישל קלפטון", url="https://shablul.smarticket.co.il/event/4833"),
        listing("secrettelavivapp", "a", "ספיישל קלפטון",
                extra={"purchase_url": "https://shablul.smarticket.co.il/%D7%A1?id=4833"}),
        listing("secrettelaviv", "b", "Eric Clapton Special",
                extra={"purchase_url": "https://shablul.smarticket.co.il/%D7%A1?id=4833"}),
        listing("makore", "c", "ספיישל קלפטון", extra={"purchase_url": "https://makore.kartisim.co.il/announce/buy/214002"}),
        listing("ogo", "d", "מחווה לאריק קלפטון", extra={"purchase_url": "https://ru.kupatbravo.co.il/announce/buy/214002"}),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1


def test_venue_box_office_hour_beats_a_guides_typo(tmp_path: Path) -> None:
    hall = {"venue_name": "שבלול"}
    _, groups = grouped(tmp_path, [
        listing("smarticket", "shablul:4833", "ספיישל קלפטון", **hall),
        listing("kupatbravo", "214002", "ספיישל קלפטון", **hall),
        listing("mevalim", "132279", "ספיישל קלפטון", start=START - timedelta(hours=6, minutes=30), **hall),
        # Two sessions at the box office: a guide's other hour may be either, so it stays apart.
        listing("smarticket", "shablul:8", "ערב סטנדאפ גדול", start=START - timedelta(hours=6), **hall),
        listing("smarticket", "shablul:9", "ערב סטנדאפ גדול", **hall),
        listing("mevalim", "9", "ערב סטנדאפ גדול", start=START - timedelta(hours=3), **hall),
    ])  # fmt: skip
    assert groups["mevalim:132279"][0] == groups["smarticket:shablul:4833"][0] == groups["kupatbravo:214002"][0]
    assert groups["mevalim:9"][0] not in (groups["smarticket:shablul:8"][0], groups["smarticket:shablul:9"][0])


def test_a_listing_without_a_price_offers_its_twins_in_event_data(tmp_path: Path) -> None:
    """Google's event data: the same party on two sites, one with no price, gets the other's price."""
    from partylinks.seo import event_jsonld

    settings, _ = grouped(tmp_path, [
        listing("goout", "a", "THURSDAY MOON | MAINSTREAM", tickets=[]),
        listing("eventer", "b", "THURSDAY MOON | MAINSTREAM", tickets=[TicketTier(name="Entry", price=Decimal(88))]),
    ])  # fmt: skip
    db = Database(settings.database.url)
    with db.session() as session:
        bare = session.get(EventRow, "goout:a")
        assert bare is not None and bare.min_price is None and bare.best_listing_id == "eventer:b"
        assert event_jsonld(bare, settings)["offers"][0]["price"] == 88


METEOR, ANAT = (32.1748383, 34.8328078), (32.1640, 34.8440)


def test_a_citys_own_page_gives_every_listing_of_its_event_its_place(tmp_path: Path) -> None:
    """Herzliya's Smarticket lists a talk at the unit's hall (מרכז הקיימות / Meteor); the city's own page
    says "קפה אנט, בן גוריון 32". The city's place wins; a listing with a place of its own never takes
    another venue's pin (it stopped the geocoder, Nov 2026)."""
    from partylinks import group_fill

    talk = "מדע על הבר: התחדשות עירונית כהזדמנות"
    settings, groups = grouped(tmp_path, [
        listing("smarticket", "3836", talk, where=METEOR, venue_name="מרכז מטאור", address=None, city="הרצליה"),
        listing("munievents", "14246", talk, where=None, venue_name="קפה אנט", address="בן גוריון 32", city="הרצליה"),
    ])  # fmt: skip
    assert groups["smarticket:3836"][0] == groups["munievents:14246"][0]
    db = Database(settings.database.url)
    places = frozenset(settings.dedup.place_sources)
    with db.session() as session:  # not geocoded yet: it keeps its own place and waits for the geocoder
        fills = {f["id"]: f for f in group_fill.plan(session, None, places)}
    assert "munievents:14246" not in fills or fills["munievents:14246"]["latitude"] is None
    with db.session() as session:
        session.get_one(EventRow, "munievents:14246").latitude, session.get_one(EventRow, "munievents:14246").longitude = ANAT
    with db.session() as session:
        fills = {f["id"]: f for f in group_fill.plan(session, None, places)}
    box_office = fills["smarticket:3836"]
    assert (box_office["venue_name"], box_office["address"]) == ("קפה אנט", "בן גוריון 32")
    assert (box_office["latitude"], box_office["longitude"]) == ANAT
    with db.session() as session:  # off: the box office keeps what it states
        assert "smarticket:3836" not in {f["id"] for f in group_fill.plan(session)}


def test_a_kids_play_tagged_kids_on_one_site_and_theatre_on_another_is_one_event(tmp_path: Path) -> None:
    """Haifa, Nov 2026: the city's page (kids) joined Smarticket and OGO, Makore (theatre) stayed out."""
    hall = {"venue_name": "בית יד לבנים", "city": "חיפה"}
    _, groups = grouped(tmp_path, [
        listing("haifamuni", "1", "הצגת ילדים: הענק וגנו – תיאטרון גושן", source_genres=["kids"], **hall),
        listing("smarticket", "2", "הענק וגנו | תיאטרון גושן", source_genres=["kids"], **hall),
        listing("makore", "3", "הענק וגנו | תיאטרון גושן", source_genres=["theatre"], **hall),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1


def test_a_guides_two_pages_of_one_show_at_one_hour_are_one_event(tmp_path: Path) -> None:
    """Makore lists the Cameri's "מדיאה" once per seller, one name longer; both join the box offices' copies."""
    hall = {"venue_name": "תיאטרון הקאמרי", "city": "תל אביב", "source_genres": ["theatre"]}
    _, groups = grouped(tmp_path, [
        listing("makore", "a", "מדיאה - תיאטרון הקאמרי", **hall),
        listing("makore", "b", "מדיאה - עם עמוס תמם וכנרת לימוני - תיאטרון הקאמרי", **hall),
        listing("kupatbravo", "c", "מדיאה - תיאטרון הקאמרי", **hall),
    ])  # fmt: skip
    assert len({g for g, _ in groups.values()}) == 1
