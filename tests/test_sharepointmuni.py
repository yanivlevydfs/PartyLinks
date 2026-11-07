"""Municipal SharePoint lists (Rishon LeZion, 7 November 2026): list rows and display forms to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import zone
from partylinks.scrapers.sharepointmuni import (
    SharePointMuniSettings,
    SharePointSite,
    parse_detail,
    parse_event,
    parse_list,
)

TZ = "Asia/Jerusalem"
FIX = Path(__file__).parent / "fixtures/sharepointmuni"
SITE = SharePointSite(
    web_url="https://www.rishonlezion.muni.il/RecreationalAndLeisure", list_id="x", view_id="y", city="ראשון לציון"
)
S = SharePointMuniSettings(cron="0 0 * * *", sites=[SITE], skip_hosts=["htrl.co.il", "smarticket.co.il"])
ROWS = {r["id"]: r for r in parse_list(json.loads((FIX / "list.json").read_text("utf-8")), TZ)}


def _detail(item: str) -> dict[str, str]:
    return parse_detail((FIX / f"item_{item}.html").read_text("utf-8"))


def test_list_rows() -> None:
    r = ROWS["7309"]
    assert r["start"] == datetime(2026, 11, 9, 19, 0, tzinfo=zone(TZ))
    assert r["end"] == datetime(2026, 11, 9, 20, 30, tzinfo=zone(TZ))
    assert r["url"] == "https://www.rishonlezion.muni.il/Lists/List3/CustomDispForm.aspx?ID=7309"
    assert r["cost"] == "בחינם" and r["types"] == ["סביבה וטבע"]
    assert r["image"] and r["image"].startswith("https://www.rishonlezion.muni.il/") and " " not in r["image"]


def test_free_event_with_registration_link_and_venue_from_address() -> None:
    e = parse_event(ROWS["7309"], _detail("7309"), SITE, S, TZ)
    assert e is not None
    assert (e.venue_name, e.address, e.city) == ("בר הרוטצ'", "בר הרוטצ', רוטשילד 64, ראשון לציון", "ראשון לציון")
    assert e.extra["purchase_url"].startswith("https://tikshuv.rishonlezion.muni.il/")
    assert e.min_price == Decimal(0) and e.tickets[0].is_free
    assert e.description and not e.description.startswith("תיאור")
    assert e.source_event_id == "www.rishonlezion.muni.il:7309"
    assert Enricher(Settings().enrichment, TZ).enrich(e).genres


def test_culture_hall_box_office_row_is_skipped() -> None:
    assert _detail("7342")["קישור לתשלום:href"].startswith("https://htrl.co.il/")
    assert parse_event(ROWS["7342"], _detail("7342"), SITE, S, TZ) is None


def test_end_equal_to_start_is_dropped() -> None:
    row = {**ROWS["7309"], "end": ROWS["7309"]["start"]}
    e = parse_event(row, _detail("7309"), SITE, S, TZ)
    assert e is not None and e.end_at is None
