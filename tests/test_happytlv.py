"""HappyTLV parser tests against real pages (October 2026), trimmed to their JSON-LD."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import happytlv

FIXTURES = Path(__file__).parent / "fixtures" / "happytlv"
TZ = ZoneInfo("Asia/Jerusalem")
SITE = "https://www.happytlv.com"
SETTINGS = happytlv.HappyTlvSettings(
    enabled=True, cron="0 5 * * *", site_url=SITE, sitemap_path="/sitemap.xml",
    parties_path="/tel-aviv-parties/", happy_hours_path="/tel-aviv-happy-hours/",
    places={"/escape-rooms/": "escape rooms", "/work-spaces/": "work spaces"},
)  # fmt: skip
BAR = f"{SITE}/tel-aviv-happy-hours/%D7%A4%D7%95%D7%A2%D7%94"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_sitemap_keeps_upcoming_parties_and_happy_hour_pages() -> None:
    parties, bars, places = happytlv.split_sitemap(page("sitemap.xml"), SETTINGS, date(2026, 10, 22))
    assert len(parties) == 10 and all("-2026-10-22" in p for p in parties)  # hubs have no date
    assert f"{SITE}/tel-aviv-happy-hours/%D7%A6'%D7%99%D7%A8%D7%A1" in bars  # &apos; decoded
    assert f"{SITE}/tel-aviv-happy-hours" not in bars and len(bars) == 7
    assert happytlv.split_sitemap(page("sitemap.xml"), SETTINGS, date(2026, 10, 25))[0] == []  # all past
    # Section roots, articles and the parties' second copy are not places; city hubs go to the parser.
    assert places == {f"{SITE}/escape-rooms/haifa": "escape rooms", f"{SITE}/work-spaces/quick": "work spaces"}


def test_party() -> None:
    url = f"{SITE}/tel-aviv-parties/jimmy-who-2026-10-29-30756"
    party = happytlv.parse_party(page("party.html"), url, "Asia/Jerusalem")
    assert party is not None
    assert (party.source_event_id, party.title) == ("jimmy-who-2026-10-29-30756", "THURSDAY JIMMYwHO X HALLOWEEN")
    assert (party.start_at, party.end_at) == (
        datetime(2026, 10, 29, 23, tzinfo=TZ),
        datetime(2026, 10, 30, 5, tzinfo=TZ),
    )
    assert (party.venue_name, party.address, party.min_age) == ("Jimmy Who?", "רוטשילד 24 תל אביב", 18)
    assert [t.price for t in party.tickets] == [Decimal(79)]
    assert party.extra == {"purchase_url": "https://www.go-out.co/event/1789464730756"}  # no HappyTLV referral


def test_happy_hour_is_one_event_per_day_it_runs() -> None:
    events = happytlv.parse_happy_hours(page("bar.html"), BAR, "Asia/Jerusalem", date(2026, 10, 22), 7)
    # Sunday to Thursday 17:00-19:00: Thu 22, Sun 25 ... Wed 28 (Fri and Sat have none).
    assert [e.start_at for e in events] == [datetime(2026, 10, d, 17, tzinfo=TZ) for d in (22, 25, 26, 27, 28)]
    first = events[0]
    assert first.end_at == datetime(2026, 10, 22, 19, tzinfo=TZ)
    assert (first.title, first.description, first.venue_name) == ("האפי האוור בפועה", "30% על שתייה ואוכל", "פועה")
    assert (first.latitude, first.address, first.source_genres) == (32.0527728, "רבי יוחנן 8", ["happy hour"])
    assert len({e.source_event_id for e in events}) == 5
    enriched = Enricher(Settings().enrichment, "Asia/Jerusalem").enrich(first)
    assert enriched is not None and "happy_hour" in enriched.genres


def test_hub_pages_are_not_bars() -> None:
    assert (
        happytlv.parse_happy_hours(
            page("hub.html"), f"{SITE}/tel-aviv-happy-hours/date", "Asia/Jerusalem", date(2026, 10, 22), 7
        )
        == []
    )


def test_place_with_hours_is_one_event_per_open_day() -> None:
    url = f"{SITE}/work-spaces/quick"
    events = happytlv.parse_place(page("work_space.html"), url, "Asia/Jerusalem", date(2026, 10, 22), 7, "work spaces")
    # Sunday to Thursday 07:00-17:00, Friday 08:00-15:00, closed Saturday: Thu 22, Fri 23, Sun 25 ... Wed 28.
    assert [e.start_at for e in events] == [
        datetime(2026, 10, 22, 7, tzinfo=TZ), datetime(2026, 10, 23, 8, tzinfo=TZ),
        *(datetime(2026, 10, d, 7, tzinfo=TZ) for d in (25, 26, 27, 28)),
    ]  # fmt: skip
    first = events[0]
    assert (first.title, first.venue_name, first.address, first.latitude) == ("Quick", "Quick", "פינסקר 11", 32.074079)
    assert first.end_at == datetime(2026, 10, 22, 17, tzinfo=TZ)
    assert first.extra["happy_hour"] is True and first.extra["website"].startswith("https://www.instagram.com/")
    assert len({e.source_event_id for e in events}) == 6
    enriched = Enricher(Settings().enrichment, "Asia/Jerusalem").enrich(first)
    assert enriched is not None and "work_spaces" in enriched.genres


def test_place_without_hours_is_all_day() -> None:
    url = f"{SITE}/escape-rooms/x"
    events = happytlv.parse_place(
        page("escape_room.html"), url, "Asia/Jerusalem", date(2026, 10, 22), 3, "escape rooms"
    )
    assert [(e.start_at, e.end_at) for e in events] == [(datetime(2026, 10, d, tzinfo=TZ), None) for d in (22, 23, 24)]
    assert events[0].title == "ססוגב חדרי בריחה ניידים" and events[0].latitude == 32.0663736
    rated = page("escape_room.html").replace("אחד העם 51, תל אביב-יפו, 6520604", "4.6 (10230 ביקורות)")
    assert happytlv.parse_place(rated, url, "Asia/Jerusalem", date(2026, 10, 22), 1, "x")[0].address is None
    enriched = Enricher(Settings().enrichment, "Asia/Jerusalem").enrich(events[0])
    assert enriched is not None and "escape_rooms" in enriched.genres


def test_team_activity_without_address_is_listed() -> None:
    url = f"{SITE}/officehappyhour/85-questions"
    node = json.dumps({"@type": "LocalBusiness", "name": "קלפים לשיחה", "url": url, "telephone": "054-5809734"})
    html = f'<script type="application/ld+json">{node}</script>'
    events = happytlv.parse_place(html, url, "Asia/Jerusalem", date(2026, 10, 22), 2, "team activities")
    assert [(e.title, e.latitude, e.extra["phone"]) for e in events] == [("קלפים לשיחה", None, "054-5809734")] * 2


def test_place_hubs_are_skipped() -> None:
    hub = f"{SITE}/escape-rooms/haifa"
    assert (
        happytlv.parse_place(page("place_hub.html"), hub, "Asia/Jerusalem", date(2026, 10, 22), 7, "escape rooms") == []
    )
