"""Old Akko (akko.org.il): the events page's cards and the event posts' API rows, trimmed real answers (28.11.2026)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.oldakko import AkkoCard, AkkoVenue, OldAkkoSettings, parse_cards, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "oldakko"
TZ = "Asia/Jerusalem"
HALLS = AkkoVenue(name="אולמות האבירים", address="ויצמן 1, עכו", latitude=32.9235884, longitude=35.0731056)
SETTINGS = OldAkkoSettings(cron="30 9,21 * * *", venues={"אולמות האבירים": HALLS}, default_venue=HALLS)


def _cards() -> list[AkkoCard]:
    return parse_cards((FIXTURES / "events.html").read_text(encoding="utf-8"))


def _posts() -> dict[int, dict]:  # type: ignore[type-arg]
    return {p["id"]: p for p in json.loads((FIXTURES / "posts.json").read_text(encoding="utf-8"))}


def test_cards() -> None:
    quiet, show = _cards()
    assert quiet.title == "שעות שקטות באולמות האבירים"
    assert quiet.when == "5,6,7/12/2026" and quiet.hours == "17:00-19:00" and quiet.price == '10 ש"ח'
    assert quiet.buy_url and quiet.buy_url.endswith("?payment_package_id")
    assert quiet.image == "https://www.akko.org.il/wp-content/uploads/2026/11/card-image-550-360-new.jpg"
    assert show.title == "מופע אור קולי חדש בעכו העתיקה" and show.hours is None


def test_card_with_three_dates_is_three_evenings() -> None:
    quiet = _cards()[0]
    events = parse_event(quiet, _posts()[27712], "https://x.test/og.jpg", SETTINGS, TZ, date(2026, 11, 28))
    assert [e.start_at.isoformat() for e in events if e.start_at] == [
        "2026-12-05T17:00:00+02:00",
        "2026-12-06T17:00:00+02:00",
        "2026-12-07T17:00:00+02:00",
    ]
    first = events[0]
    assert first.end_at is not None and first.end_at.hour == 19
    # Its page's slug, not the post id (the post comes from an optional call): the same id with or without it.
    assert first.source_event_id.startswith("שעות-שקטות") and first.source_event_id.endswith("-2026-12-05")
    assert first.venue_name == "אולמות האבירים" and first.city == "עכו"
    assert first.tickets[0].price == Decimal(10) and first.images[0] == "https://x.test/og.jpg"
    assert first.description and "בלי ההמולה" in first.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None and enriched.genres and enriched.city == "עכו"


def test_undated_card_is_no_event() -> None:
    show = _cards()[1]
    assert parse_event(show, None, None, SETTINGS, TZ, date(2026, 11, 28)) == []


def test_post_no_card_shows_gives_its_dates_from_the_text() -> None:
    events = parse_event(None, _posts()[26645], None, SETTINGS, TZ, date(2025, 12, 1))
    assert [e.start_at.date() for e in events if e.start_at] == [date(2025, 12, d) for d in (18, 19, 20, 21)]
    assert events[0].start_at is not None and events[0].start_at.hour == 17 and events[0].start_at.minute == 30
    assert events[0].end_at is not None and events[0].end_at.hour == 23
    assert events[0].title == "חנוכריסמס 2025" and [str(t.price) for t in events[0].tickets] == ["30"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(events[0])
    assert enriched is not None and {"hanukkah", "christmas"} & set(enriched.genres)
