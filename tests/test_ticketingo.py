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
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-09T18:00:00+00:00" and e.time_zone == "Europe/London"
    assert (e.country, e.city, e.venue_name) == ("GB", "קארדיף", "קארדיף אינטרנשיונל ארנה")
    assert (e.latitude, e.longitude) == (51.4789, -3.17167) and e.lineup == ["Papa Roach"]
    assert str(e.url) == "https://ticketingo.co.il/event/406514" and e.extra["purchase_url"] == str(e.url)
    assert e.source_genres == ["הופעות"]  # not the type "בידור", which every row has
    settings = Settings()
    assert "live_show" in Enricher(settings.enrichment, settings.timezone).enrich(e).genres  # type: ignore[union-attr]


def test_wall_clock_is_local_not_utc() -> None:
    tampa = parse_event(ROWS[1], S, TZ)  # dateISO 19:30 "+00:00" in Florida
    assert tampa is not None and tampa.time_zone == "America/New_York"
    assert tampa.start_at is not None and tampa.start_at.isoformat() == "2026-11-09T19:30:00-05:00" and tampa.country == "US"


def test_unknown_venue_and_unknown_time() -> None:
    tbd = parse_event(ROWS[2], S, TZ)
    assert tbd is not None and tbd.venue_name is None
    assert parse_event(ROWS[3], S, TZ) is None  # date not final yet: no guessed date on the site
    timeless = parse_event(ROWS[0] | {"finalTime": False}, S, TZ)
    assert timeless is not None and not timeless.time_known
    assert _country('ארה"ב (ארצות הברית)') == "US" and _country(None) is None


SPORT = {
    r["id"]: r
    for r in json.loads((Path(__file__).parent / "fixtures/ticketingo/sport.json").read_text("utf-8"))["data"]
}


def test_sport_rows_are_matches_with_their_league() -> None:
    nfl = parse_event(SPORT[387034], S, TZ)
    assert nfl is not None and nfl.title == "וושינגטון קומנדרס - סינסינטי בנגלס" and nfl.lineup == []
    assert nfl.source_genres == ["ספורט", "פוטבול", "NFL (פוטבול)"] and nfl.country == "US"
    settings = Settings()
    assert "sport" in Enricher(settings.enrichment, settings.timezone).enrich(nfl).genres  # type: ignore[union-attr]
    ascot = parse_event(SPORT[404259], S, TZ)  # a race day names only itself: the meeting goes first
    assert ascot is not None and ascot.title == "רויאל אסקוט - היום הראשון" and ascot.country == "GB"
    abu_dhabi = parse_event(SPORT[387903], S, TZ)  # its after-race concerts on the lines below: left out
    assert abu_dhabi is not None and abu_dhabi.title == "פורמולה 1 - גרנד פרי אבו דאבי"
    assert parse_event(SPORT[327121], S, TZ) is None  # date not final
