"""Eventbuzz parser on trimmed real pages (no network)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.models import TierStatus
from partylinks.scrapers.eventbuzz import EventbuzzSettings, parse_event, parse_listing

FIXTURES = Path(__file__).parent / "fixtures" / "eventbuzz"
TZ = "Asia/Jerusalem"
# Mirrors the `sources.eventbuzz` block of config/settings.yaml.
CFG = EventbuzzSettings.model_validate(
    {
        "cron": "30 * * * *",
        "site_url": "https://eventbuzz.co.il",
        "listing_path": "/events",
        "event_path_prefix": "/lp/event/",
        "selectors": {
            "card_link": ".content-element a[href]",
            "info_items": ".detailsDiv .text-center",
            "description": ".descDiv",
            "ticket_row": 'tr[id^="tid-"]',
            "ticket_name": "td",
            "ticket_price": "input.priceTag",
            "ticket_original_price": "input.origPriceTag",
            "ticket_remaining": "input.tickAvailable",
            "ticket_on_sale": "select.tQuantity",
            "service_fee": "#servicefeesum",
        },
        "labels": {"start": "התחלה", "location": "מיקום"},
    }
)


def test_listing_urls() -> None:
    urls = parse_listing((FIXTURES / "listing.html").read_text(encoding="utf-8"), CFG)
    assert urls == [
        "https://eventbuzz.co.il/lp/event/dhpco",
        "https://eventbuzz.co.il/lp/event/openmic-20-10",
        "https://eventbuzz.co.il/lp/event/alternativathelastsinparty",
    ]


def test_parse_event() -> None:
    url = "https://eventbuzz.co.il/lp/event/alternativathelastsinparty"
    event = parse_event((FIXTURES / "event.html").read_text(encoding="utf-8"), url, CFG, TZ)

    assert event.source_event_id == "6a89d309a46da1d06f8b4573"
    assert event.title == "ALTERNATIVA! ROCK & METAL PARTY"
    assert event.start_at == datetime(2026, 10, 22, 22, 0, tzinfo=ZoneInfo(TZ))
    assert event.venue_name == "מועדון הגגרין - אולם נובה"
    assert event.address == "קיבוץ גלויות 13, תל אביב"
    assert event.city == "תל אביב"
    assert event.image_url == "https://cdnpad.eventbuzz.co.il/1fc79cf70008d071bac206667f7b2172.jpg"
    assert event.description and "DJ SHELIX" in event.description

    early, regular = event.tickets
    assert early.source_ticket_id == "6a89d309a46da1d06f8b4574"
    assert early.name == "מכירה מוקדמת"
    assert early.price == Decimal(40) and early.status is TierStatus.CLOSED
    assert regular.price == Decimal(60) and regular.status is TierStatus.ON_SALE
    assert regular.remaining == 30
    assert (event.min_price, event.max_price) == (Decimal(40), Decimal(60))
    assert not event.is_sold_out


def test_unlisted_events_are_found_in_the_wayback_index_and_a_search() -> None:
    from datetime import UTC, datetime

    from partylinks.scrapers import eventbuzz_discover as discover

    cdx = "http://eventbuzz.co.il/lp/event/vhs\nhttps://eventbuzz.co.il/lp/event/vhs?lang=en&\nhttps://eventbuzz.co.il/lp/event/\n"
    ddg = '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Feventbuzz.co.il%2Flp%2Fevent%2Fx">eventbuzz.co.il/lp/event/kinorai-burlesque-night</a>'
    assert discover.slugs(cdx) == ["vhs"] and discover.slugs(ddg) == ["kinorai-burlesque-night"]
    assert discover.wayback_url("cdx?from={since}", 10, datetime(2026, 11, 12, tzinfo=UTC)) == "cdx?from=20261102"
    assert discover.search_urls("s?q={q}&s={offset}", "site:x", 2) == ["s?q=site%3Ax&s=0", "s?q=site%3Ax&s=30"]
