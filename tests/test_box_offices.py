"""Box offices: each Smarticket tenant counted by the host of its events' URLs (no database needed)."""

from __future__ import annotations

from typing import Any

from partylinks.box_offices import box_offices
from partylinks.scrapers.smarticket import SmarticketSettings, SmarticketTenant

SETTINGS = SmarticketSettings(
    cron="8 */4 * * *",
    tenant_domain="smarticket.co.il",
    shows_path="/api/shows",
    event_page_path="/event/{id}",
    uploads_path="/uploads/",
    calendar_path="/api/show_theater/get_events_calendar",
    tenants=[
        SmarticketTenant(host="htk", venue_name="היכל התרבות כרמיאל", city="כרמיאל"),
        SmarticketTenant(host="tickets.raanana.muni.il", venue_name="רעננה", city="רעננה"),
        SmarticketTenant(host="dolev", venue_name='מתנ"ס קריית אונו'),
    ],
)


class _Session:
    def __init__(self, rows: list[tuple[str, str]], source: str = "smarticket") -> None:
        # (group, url) rows of one source -> the query's (source, group, url, city, organizer) rows
        self.rows = [(source, *r, None, None) if len(r) == 2 else r for r in rows]

    def execute(self, _: Any) -> _Session:
        return self

    def all(self) -> list[Any]:
        return self.rows


def test_counts_each_event_group_once_per_tenant() -> None:
    rows = [
        ("g1", "https://htk.smarticket.co.il/event/1"),
        ("g1", "https://htk.smarticket.co.il/event/1"),  # the same group twice: one event
        ("g2", "https://htk.smarticket.co.il/event/2"),
        ("g3", "https://tickets.raanana.muni.il/event/9"),
    ]
    offices = box_offices(_Session(rows), {"smarticket": SETTINGS})  # type: ignore[arg-type]
    assert [(o.name, o.events, o.url) for o in offices] == [
        ("היכל התרבות כרמיאל", 2, "https://htk.smarticket.co.il"),
        ("רעננה", 1, "https://tickets.raanana.muni.il"),
        ('מתנ"ס קריית אונו', 0, "https://dolev.smarticket.co.il"),  # no upcoming events yet: still listed
    ]
    assert offices[0].city == "כרמיאל" and offices[2].city is None


def test_no_smarticket_source_no_offices() -> None:
    assert box_offices(_Session([]), {}) == []  # type: ignore[arg-type]
    off = SETTINGS.model_copy(update={"enabled": False})
    assert box_offices(_Session([]), {"smarticket": off}) == []  # type: ignore[arg-type]


def test_pres_hosts_and_city_sites_are_stores_too() -> None:
    from partylinks.scrapers.munievents import MuniEventsSettings, MuniSite
    from partylinks.scrapers.pres import PresHost, PresSettings

    pres = PresSettings(
        cron="1 * * * *",
        presentations_path="/api/presentations/",
        features_path="/api/features/",
        ticket_groups_path="/api/presentations/{id}/ticketGroups",
        event_page_path="/order/{id}",
        hosts=[PresHost(key="muza", url="https://muza.pres.global", venue="מוזיאון ארץ ישראל", city="תל אביב")],
    )
    muni = MuniEventsSettings(cron="2 * * * *", sites=[MuniSite(url="https://www.eilat.muni.il", city="אילת")])
    rows = [("g1", "https://muza.pres.global/order/7")]
    offices = box_offices(_Session(rows, "pres"), {"pres": pres, "munievents": muni})  # type: ignore[arg-type]
    assert [(o.source, o.name, o.city, o.url, o.events) for o in offices] == [
        ("pres", "מוזיאון ארץ ישראל", "תל אביב", "https://muza.pres.global", 1),
        ("munievents", "עיריית אילת", "אילת", "https://www.eilat.muni.il", 0),  # MUZA's event is not Eilat's
    ]


def test_path_branch_and_seller_host_stores() -> None:
    from partylinks.config import get_settings
    from partylinks.scrapers import configured_sources
    from partylinks.scrapers.grayclub import GrayclubSettings
    from partylinks.scrapers.tickettailor import TicketTailorSettings

    real = configured_sources(get_settings())
    tt = TicketTailorSettings.model_validate(
        {**real["tickettailor"].model_dump(), "organizers": ["gilliaptercomedy", "kii"]}
    )
    gray = GrayclubSettings.model_validate(
        {**real["grayclub"].model_dump(), "site_url": "https://grayclub.co.il",
         "branches": [{"path": "/gray-yehud/", "city": "יהוד"}, {"path": "/gray-tlv/", "city": "תל אביב-יפו"}]}
    )  # fmt: skip
    rows: list[Any] = [
        ("tickettailor", "g1", "https://www.tickettailor.com/events/gilliaptercomedy/1", None, "GILLI APTER COMEDY"),
        ("tickettailor", "g2", "https://www.tickettailor.com/events/gilliaptercomedy/2", None, "GILLI APTER COMEDY"),
        ("grayclub", "g3", "https://grayclub.co.il/event/1/2/", "תל אביב", None),
        ("grayclub", "g4", "https://grayclub.co.il/event/1/3/", "יהוד", None),
    ]
    rows += [
        ("cinematheque_tlv", f"c{i}", f"https://tickets.seller.example.co.il/order/{i}", "תל אביב", None)
        for i in range(12)
    ]
    rows += [("cinematheque_tlv", "c99", "https://www.cinema.co.il/event/99", "תל אביב", None)]  # its own site
    rows += [
        ("tickettailor", f"y{i}", f"https://grayclub.co.il/event/9/{i}/", None, None) for i in range(11)
    ]  # Gray's own site
    rows += [("tickettailor", f"x{i}", f"https://www.tickettailor.com/events/other/{i}", None, None) for i in range(3)]
    cin = real["cinematheque_tlv"]  # site_url www.cinema.co.il
    sources: dict[str, Any] = {"tickettailor": tt, "grayclub": gray, "cinematheque_tlv": cin}
    offices = {o.url: o for o in box_offices(_Session(rows), sources)}  # type: ignore[arg-type]
    gilli = offices["https://www.tickettailor.com/events/gilliaptercomedy"]
    assert (gilli.name, gilli.events) == ("GILLI APTER COMEDY", 2)
    assert offices["https://www.tickettailor.com/events/kii"].events == 0  # still listed
    assert offices["https://grayclub.co.il/gray-tlv/"].events == 1  # "תל אביב" is "תל אביב-יפו"
    assert offices["https://grayclub.co.il/gray-yehud/"].name == "Gray Club יהוד"
    seller = offices["https://tickets.seller.example.co.il"]
    assert (seller.source, seller.events, seller.city) == ("cinematheque_tlv", 12, "תל אביב")
    assert "https://www.cinema.co.il" not in offices  # the source's own site is no extra store
    assert "https://grayclub.co.il" not in offices  # another source's own site: a store already
    assert "https://www.tickettailor.com" not in offices  # 3 events of an unlisted organizer: below the floor


def test_brand_front_doors_and_tenants() -> None:
    from partylinks.box_offices import _brand

    assert _brand("eventbrite.co.uk") == ("eventbrite", True)
    assert _brand("eventbrite.com.au") == ("eventbrite", True)
    assert _brand("tickchak.co.il") == _brand("live.tickchak.co.il") == ("tickchak", True)
    assert _brand("gveret-rabia.tickchak.co.il") == ("tickchak", False)  # a producer's own box office
