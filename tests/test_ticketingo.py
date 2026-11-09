"""Ticketingo: the entertainment API rows to ScrapedEvent, in the venue's own time zone."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ticketingo import TicketingoSettings, _country, parse_event

TZ = "Asia/Jerusalem"
ROWS = json.loads((Path(__file__).parent / "fixtures/ticketingo/page1.json").read_text("utf-8"))["data"]
S = TicketingoSettings(cron="16 10,22 * * *")


def test_concert_in_the_venues_zone() -> None:
    e = parse_event(ROWS[0], S, TZ)
    assert e is not None and e.source_event_id == "406514" and e.title == "Papa Roach-קארדיף, וויילס"
    assert e.start_at.isoformat() == "2026-11-09T18:00:00+00:00" and e.time_zone == "Europe/London"
    assert (e.country, e.city, e.venue_name) == ("GB", "קארדיף", "קארדיף אינטרנשיונל ארנה")
    assert (e.latitude, e.longitude) == (51.4789, -3.17167) and e.lineup == ["Papa Roach"]
    assert str(e.url) == "https://ticketingo.co.il/event/406514" and e.extra["purchase_url"] == str(e.url)
    assert e.source_genres == ["הופעות"]  # not the type "בידור", which every row has
    settings = Settings()
    assert "live_show" in Enricher(settings.enrichment, settings.timezone).enrich(e).genres  # type: ignore[union-attr]


def test_wall_clock_is_local_not_utc() -> None:
    tampa = parse_event(ROWS[1], S, TZ)  # dateISO 19:30 "+00:00" in Florida
    assert tampa is not None and tampa.time_zone == "America/New_York"
    assert tampa.start_at.isoformat() == "2026-11-09T19:30:00-05:00" and tampa.country == "US"


def test_unknown_venue_and_unknown_time() -> None:
    tbd = parse_event(ROWS[2], S, TZ)
    assert tbd is not None and tbd.venue_name is None
    assert parse_event(ROWS[3], S, TZ) is None  # date not final yet: no guessed date on the site
    timeless = parse_event(ROWS[0] | {"finalTime": False}, S, TZ)
    assert timeless is not None and not timeless.time_known
    assert _country('ארה"ב (ארצות הברית)') == "US" and _country(None) is None
