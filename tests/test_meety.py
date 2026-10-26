from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.meety import parse_event, parse_interests, parse_listing

LISTING = """<select id="interests" name="i"><option selected value="">חפש תחום עניין</option>
<option value="salsa">סלסה- ריקודים לטיניים</option><option value="folk dances">ריקודי עם</option></select>
<div onclick='location.href="/events/2895"'><a href="/events/2895">מסיבת סלסה</a></div>
<div><a href="/events/2913">ריקודי עם בספורטן</a></div>"""

PAGE = """<div class="Path-1185">מסיבת סלסה כל יום שני בלטינו מיינד חיפה.</div>
<div class="taskbartext dateConvert" data-eventdate="2026-10-26 21:00:00"></div>
<div class="w-3/5"><div><p>הדרכות למתחילים - בצ'אטה וסלסה</p></div></div>
<div class="w-2/5"><img class="absolute rounded-2xl w-full" src="/images/events_images/1666264158Untitledיחעגד.png"></div>
<div onclick=window.location.href="https://maps.google.com/?q=%D7%94%D7%9E%D7%95%D7%A1%D7%9B%D7%99%D7%9D+24%2C+%D7%97%D7%99%D7%A4%D7%94">"""


def test_listing_and_event_page() -> None:
    assert parse_interests(LISTING) == [("salsa", "סלסה- ריקודים לטיניים"), ("folk dances", "ריקודי עם")]
    assert list(parse_listing(LISTING)) == ["2895", "2913"]
    event = parse_event("2895", "https://meety.co.il/events/2895", PAGE, ["סלסה- ריקודים לטיניים"],
                        "https://meety.co.il", "Asia/Jerusalem")  # fmt: skip
    assert event is not None
    assert event.start_at is not None and event.start_at.isoformat() == "2026-10-26T21:00:00+02:00"
    assert (event.address, event.city) == ("המוסכים 24, חיפה", "חיפה")
    assert event.images[0].startswith("https://meety.co.il/images/events_images/1666264158Untitled%D7%99")
    assert "בצ'אטה" in (event.description or "")
    genres = Enricher(Settings().enrichment, "Asia/Jerusalem")._detect_genres(event)
    assert {"latin_dance", "salsa"} <= set(genres)
    assert parse_event("1", "https://meety.co.il/events/1", "<div></div>", [], "https://meety.co.il", "UTC") is None
