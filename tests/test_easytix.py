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
