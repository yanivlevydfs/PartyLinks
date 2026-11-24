"""Buki Naeh: tour dates from the wp-json pages' text."""

from __future__ import annotations

from datetime import date

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.buki import BukiSettings, build_event, intro, tour_dates

CFG = BukiSettings(cron="0 0 * * *")

# The tours page's blocks, as wp-json renders them (24.11.2026).
HTML = (
    '<h2 style="text-align: center;"><strong><span style="color: #333333;">טיולי פלילים בתל אביב &#8211; '
    'התאריכים הקרובים:</span></strong></h2><h2 style="text-align: center;"><strong>יום חמישי &#8211; 24.12.26<br />'
    '</strong></h2><h2 style="text-align: center;"><strong>יום חמישי &#8211; 28.1.27</strong></h2>'
    '<h4 dir="rtl"><span>סיפורי שוטרים וגנבים ומה שקורה מאחורי הקלעים של עולם הפשע בישראל. בוקי מגלה את האמת '
    "המפתיעה ומגיש את הסיפורים באופן קולח, מרתק ומלווה בהומור רב.</span></h4>"
    "<p>תאריכי הטיולים הקרובים:</p><p>12.3.20 &#8211; יום חמישי</p><p>יום חמישי &#8211; 24.12.26</p>"
    "<p>מדיניות פרטיות עודכנה 5.10.25</p>"
)
TOUR = {
    "id": 2405,
    "link": "https://buki.co.il/%d7%a1%d7%99%d7%95%d7%a8%d7%99-%d7%94%d7%9c%d7%99%d7%9c%d7%94/",
    "content": {"rendered": HTML},
}


def test_tour_dates_both_orders_once_each() -> None:
    # A date without its weekday (an "updated" line) is no tour; a repeated date is one.
    assert tour_dates(HTML) == [date(2026, 12, 24), date(2027, 1, 28), date(2020, 3, 12)]
    assert tour_dates("<p>יום שישי – 31.2.27</p><p>יום שבת - 3.4.2027</p>") == [date(2027, 4, 3)]
    assert tour_dates("<p>אין תאריכים</p>") == []


def test_event() -> None:
    event = build_event(date(2026, 12, 24), TOUR, CFG, "Asia/Jerusalem")
    assert event.source_event_id == "2026-12-24"
    assert event.title == "סיורי לילה בחצר האחורית של תל אביב עם בוקי נאה"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-24T00:00:00+02:00"
    assert event.time_known is False and event.end_at is None
    assert (event.venue_name, event.city, event.latitude) == (None, "תל אביב", None)
    assert event.description is not None and event.description.startswith("סיפורי שוטרים וגנבים")
    assert event.source_genres == ["סיורים"]
    assert event.extra["purchase_url"] == TOUR["link"] == str(event.url)
    assert [str(i) for i in event.images] == [CFG.image_url]
    settings = Settings()
    assert "tours" in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)


def test_no_tour_page_falls_back_to_site() -> None:
    event = build_event(date(2027, 1, 28), {}, CFG, "Asia/Jerusalem")
    assert str(event.url) == "https://buki.co.il/"
    assert event.description is None
    assert intro("<p>קצר</p>", 60) is None
