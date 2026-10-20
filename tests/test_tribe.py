"""Club sites on The Events Calendar (Gagarin): JSON API events to ScrapedEvent."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.tribe import TribeSettings, parse_event

TZ = "Asia/Jerusalem"
EVENTS = {e["id"]: e for e in json.loads((Path(__file__).parent / "fixtures/gagarin_events.json").read_text("utf-8"))["events"]}
SETTINGS = TribeSettings(
    cron="46 8,20 * * *", site_url="https://gagarin.co.il", venue_name="גגרין | Gagarin Tel Aviv",
    address="קיבוץ גלויות 13, תל אביב-יפו", city="תל אביב-יפו",
    ticket_hosts=["go-out.co", "eventbuzz.co.il"], skip_categories=["בקרוב", "כרטיסים זמינים לרכישה"],
)  # fmt: skip


def test_event_with_price_range_and_local_times() -> None:
    e = parse_event(EVENTS[35033], SETTINGS, "gagarin", TZ)
    assert e is not None
    assert e.title == "Alien Frequency" and e.source_event_id == "35033"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-26T23:30:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-27T08:00:00+02:00"
    assert (e.min_price, e.max_price) == (Decimal(80), Decimal(100))
    assert [t.price for t in e.tickets] == [Decimal(80), Decimal(100)]
    assert e.venue_name == "גגרין | Gagarin Tel Aviv" and e.city == "תל אביב-יפו"
    assert e.event_types == ["מסיבות"]  # status labels dropped
    assert e.images and e.images[0].startswith("https://gagarin.co.il/wp-content/uploads/")


def test_seller_link_in_description_becomes_purchase_url() -> None:
    party = parse_event(EVENTS[34158], SETTINGS, "gagarin", TZ)
    rock = parse_event(EVENTS[34479], SETTINGS, "gagarin", TZ)
    assert party is not None and party.extra["purchase_url"].startswith("https://go-out.co/event/1790460344734")
    assert rock is not None and rock.extra["purchase_url"] == "https://eventbuzz.co.il/lp/event/alternativathelastsinparty"
    assert rock.title == "ALTERNATIVA! ROCK & METAL PARTY"  # HTML entities decoded
    assert rock.tickets == []  # no price on the site


def test_hidden_events_are_skipped() -> None:
    assert parse_event({**EVENTS[35033], "hide_from_listings": True}, SETTINGS, "gagarin", TZ) is None
