"""Descriptions are tidied whatever the source sent; times abroad are the venue's; a date alone has no time."""

from datetime import UTC, datetime

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import has_time, read_clock, tidy_text
from partylinks.places import city_abroad, city_zone


def test_spaces_lines_and_marks() -> None:
    raw = "  מסיבה\xa0\xa0גדולה\t בתל אביב  \r\n\r\n\r\n\u200f  שורה\u2009שנייה \n\n\n\n"
    assert tidy_text(raw) == "מסיבה גדולה בתל אביב\n\nשורה שנייה"


def test_markup_and_entities_left_as_text() -> None:
    assert tidy_text("שורה<br>שנייה<br/><p>פסקה</p>") == "שורה\nשנייה\nפסקה"
    assert tidy_text("Tom &amp; Jerry&nbsp;&quot;live&quot;") == 'Tom & Jerry "live"'
    assert tidy_text("a < b and c > d") == "a < b and c > d"  # no tag: left alone
    assert tidy_text("cafÃ© &lt;b&gt;live&lt;/b&gt;") == "café live"  # broken encoding, escaped markup


def test_punctuation_and_repeats() -> None:
    assert tidy_text("שלום , עולם . ( מוזמנים ) :)") == "שלום, עולם. (מוזמנים) :)"
    assert tidy_text("כרטיסים בקופה\nכרטיסים בקופה\nסוף") == "כרטיסים בקופה\nסוף"
    assert tidy_text("כותרת\n\nכותרת") == "כותרת\n\nכותרת"  # a blank line apart: on purpose
    assert tidy_text(" \n\u200b \n") is None


def test_description_tidied_on_the_model() -> None:
    event = ScrapedEvent(source="x", source_event_id="1", url="https://a.b", title="t", description="  a  \n\n\n\n b ")
    assert event.description == "a\n\nb"


def test_city_with_extras() -> None:
    assert city_abroad("לונדון, אנגליה", "GB")[1][0] == "London"
    assert city_abroad("Saint-Denis (Paris)", "FR")[1][0] == "Paris"
    assert city_zone("ניו יורק", "US") == "America/New_York"
    assert city_zone("Los Angeles", "US") == "America/Los_Angeles"


def test_venue_time_and_date_alone() -> None:
    settings = get_settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    base = {"source": "x", "url": "https://a.b", "title": "t", "country": "DE", "city": "ברלין"}
    party = enricher.enrich(ScrapedEvent(**base, source_event_id="1", start_at=datetime(2026, 11, 6, 22, tzinfo=UTC)))
    assert party.time_zone == "Europe/Berlin"
    assert party.local_date.isoformat() == "2026-11-06"  # 23:00 in Berlin, already the 7th in Israel
    assert party.time_slot is not None
    day = datetime.fromisoformat("2026-11-06T00:00:00+02:00")  # a date alone, read as Israel's midnight
    show = enricher.enrich(ScrapedEvent(**base, source_event_id="2", start_at=day, time_known=False))
    assert show.local_date.isoformat() == "2026-11-06" and show.time_slot is None
    assert not has_time("2026-11-06") and has_time("2026-11-06T20:00:00")
    hour = enricher.enrich(ScrapedEvent(**base, source_event_id="3", start_at=day.replace(hour=21), time_known=False))
    assert hour.time_known  # an hour is there: never turned into an all-day event


def test_read_clock() -> None:
    assert [str(read_clock(v)) for v in ("21:00", "20:30 ", "8:50 PM", "12:15 am", "בשעה 19:30", "0:30")] == [
        "21:00:00", "20:30:00", "20:50:00", "00:15:00", "19:30:00", "00:30:00",
    ]  # fmt: skip
    assert read_clock("") is None and read_clock("TBA") is None and read_clock("2026-11-06") is None
