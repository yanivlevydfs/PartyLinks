"""Be'er Sheva Theatre: performance links and iframe-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.beershevatheatre import parse_performance, performance_ids

PAGE = (Path(__file__).parent / "fixtures/beershevatheatre/event_9324.html").read_text("utf-8")
BASE = "https://b7t.smarticket.co.il"


def test_links_from_both_url_shapes() -> None:
    html = f'<a href="{BASE}//iframe/event/9324">x</a> <a href="{BASE}/event/9176">y</a> <a href="{BASE}/event/9324">'
    assert performance_ids(html) == ["9324", "9176"]


def test_sold_out_performance() -> None:
    e = parse_performance(PAGE, "9324", BASE, "Asia/Jerusalem")
    assert e is not None and e.title == "מישהו לרוץ איתו" and e.url.unicode_string() == f"{BASE}/event/9324"
    assert e.start_at.isoformat() == "2026-10-26T20:30:00+02:00" and e.end_at is not None
    assert e.address == "שדרות יצחק רגר 41, באר שבע" and e.extra["hall"] == "אולם 1"
    assert e.min_price == Decimal(90) and e.is_sold_out and e.tickets[0].status is TierStatus.SOLD_OUT
    assert e.event_types == ["theatre", "תיאטרון בחירה"]
    assert e.images and "//uploads" not in e.images[0].split("://", 1)[1]
