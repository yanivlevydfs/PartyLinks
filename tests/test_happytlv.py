"""HappyTLV parser tests against real pages (October 2026), trimmed to their JSON-LD."""

from __future__ import annotations

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
)  # fmt: skip
BAR = f"{SITE}/tel-aviv-happy-hours/%D7%A4%D7%95%D7%A2%D7%94"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_sitemap_keeps_upcoming_parties_and_happy_hour_pages() -> None:
    parties, bars = happytlv.split_sitemap(page("sitemap.xml"), SETTINGS, date(2026, 10, 22))
    assert len(parties) == 10 and all("-2026-10-22" in p for p in parties)  # hubs have no date
    assert f"{SITE}/tel-aviv-happy-hours/%D7%A6'%D7%99%D7%A8%D7%A1" in bars  # &apos; decoded
    assert f"{SITE}/tel-aviv-happy-hours" not in bars and len(bars) == 7
    assert happytlv.split_sitemap(page("sitemap.xml"), SETTINGS, date(2026, 10, 25))[0] == []  # all past


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
