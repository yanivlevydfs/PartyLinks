"""Kehila Lemaan Hateva (4nature.org.il): dated Elementor blocks on the home page and on its event posts."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.fournature import FourNatureSettings, Page, event_date, event_links, kinds, parse_page

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "fournature"
HOME = (FIXTURES / "home.html").read_text(encoding="utf-8")  # www.4nature.org.il/ (24.11.2026)
BEE = (FIXTURES / "beepark.html").read_text(encoding="utf-8")  # /events/beepark18-6/ (24.11.2026)
SETTINGS = FourNatureSettings.model_validate(
    {
        "cron": "2 6,18 * * *",
        "places": [
            {
                "match": ["מרכז הקיימות", "אלתרמן 33"],
                "venue_name": "מרכז הקיימות הרצליה",
                "address": "נתן אלתרמן 31, הרצליה",
                "latitude": 32.1745136,
                "longitude": 34.8336406,
            },
            {"match": ["פארק הרצליה"], "venue_name": "פארק הרצליה", "latitude": 32.1681433, "longitude": 34.8225986},
        ],
    }
)


def test_home_page_meetings() -> None:
    events = parse_page(Page("https://www.4nature.org.il/", HOME, date(2026, 11, 18)), SETTINGS, TZ)
    by_title = {e.title: e for e in events}
    wasp = by_title["להכיר את הצרעה – מפגש קהילה, טבע ועיר מס 4"]
    assert wasp.start_at == datetime(2026, 12, 7, 19, 0, tzinfo=IL)
    assert wasp.end_at == datetime(2026, 12, 7, 21, 0, tzinfo=IL)  # a start alone: the default two hours
    assert wasp.time_known
    assert (wasp.venue_name, wasp.city) == ("מרכז הקיימות הרצליה", "הרצליה")
    assert (wasp.latitude, wasp.longitude) == (32.1745136, 34.8336406)
    assert wasp.extra["purchase_url"] == "https://forms.gle/usokkHpcjmTBDMPZ7"  # the sign-up form
    assert str(wasp.url) == "https://forms.gle/usokkHpcjmTBDMPZ7"  # our button (/go) leads to it
    assert "[להרשמה](https://forms.gle/usokkHpcjmTBDMPZ7)" in (wasp.description or "")  # and the text has it too
    assert wasp.min_age == 16
    assert wasp.tickets and wasp.tickets[0].is_free
    assert wasp.source_genres == ["הרצאה"]
    assert wasp.images == ["https://www.4nature.org.il/wp-content/uploads/natural-reserve-g6c1a4efc0_640.jpg"]
    # A yearless date on a page changed on 18.11.2026 is that year's; its range is the start and the end.
    [volunteers] = [e for e in events if e.title.startswith("הרצאה לפעילים ומתנדבים")]
    assert volunteers.start_at == datetime(2026, 10, 24, 9, 0, tzinfo=IL)
    assert volunteers.end_at == datetime(2026, 10, 24, 11, 0, tzinfo=IL)
    assert "https://www.4nature.org.il/wp-content/uploads/course006.jpg" in volunteers.images  # its container's


def test_a_post_is_one_event_on_its_own_date() -> None:
    url = "https://www.4nature.org.il/events/beepark18-6/"
    [event] = parse_page(Page(url, BEE, date(2023, 5, 14)), SETTINGS, TZ)
    assert event.title == "סיור בממלכת הדבורים ובבית מלון לחרקים"
    assert event.start_at == datetime(2024, 7, 8, 17, 30, tzinfo=IL)
    assert event.venue_name == "פארק הרצליה"
    assert str(event.url) == url
    assert event.source_genres == ["סיור"]
    assert event.images


def test_dates() -> None:
    s = SETTINGS
    assert event_date("יום ו' 16.2.24, תאריך חלופי ב1.3.24", date(2024, 1, 1), s)[0] == date(2024, 2, 16)  # type: ignore[index]
    assert event_date("תאריך חלופי 1.3.24 בלבד", date(2024, 1, 1), s) is None  # only the rain date
    assert event_date("ביום 1.3 משעה 11:15", date(2024, 1, 30), s)[0] == date(2024, 3, 1)  # type: ignore[index]
    assert event_date("ביום 5.1", date(2026, 11, 18), s)[0] == date(2027, 1, 5)  # type: ignore[index]
    assert event_date("no date here", date(2026, 1, 1), s) is None


def test_kinds_and_links() -> None:
    assert kinds("סיור לילה", "הרצאה", SETTINGS) == ["סיור"]  # the title first
    assert kinds("להכיר את הצרעה", "הרצאה מרתקת", SETTINGS) == ["הרצאה"]
    links = event_links(HOME, SETTINGS)
    assert "https://www.4nature.org.il/events/rescue-course/" in links
    assert not any("/category/" in link for link in links)


def test_enricher_gives_site_genres() -> None:
    [wasp] = [
        e
        for e in parse_page(Page("https://www.4nature.org.il/", HOME, date(2026, 11, 18)), SETTINGS, TZ)
        if "הצרעה" in e.title
    ]
    enriched = Enricher(Settings().enrichment, TZ).enrich(wasp)
    assert enriched is not None
    assert "lectures" in enriched.genres


def test_undated_offers_of_the_home_page_and_the_listing() -> None:
    from partylinks.scrapers.fournature import undated, undated_listing

    listing = (FIXTURES / "events.html").read_text(encoding="utf-8")  # /events/ (24.11.2026)
    others = {"https://www.4nature.org.il/events/rescue-course/": "<p>מרכז הקיימות, נתן אלטרמן 31</p>"}
    home = {
        e.title: e for e in undated(Page("https://www.4nature.org.il/", HOME, date(2026, 11, 18)), others, SETTINGS)
    }
    course = home["קורס הכרות, שמירה והצלת בעלי חיים עירוניים"]
    assert course.start_at is None and not course.time_known
    assert str(course.url) == "https://www.4nature.org.il/events/rescue-course/"
    assert course.venue_name == "מרכז הקיימות הרצליה"  # named on the course's own page
    assert "הכשרות, קורסים וסדנאות לארגונים, קבוצות ורשויות: חיות, עיר וקהילה" in home  # shares the course's box
    assert "להכיר את הצרעה – מפגש קהילה, טבע ועיר מס 4" not in home  # dated: parse_page's
    assert not any("אריאלה" in t for t in home)  # the dated volunteers' lecture after the trainings' button
    page = Page("https://www.4nature.org.il/events/", listing, date(2026, 11, 24))
    titles = [e.title for e in undated_listing(page, others, SETTINGS)]
    assert "ערב טבע עירוני – סדרת הרצאות על טבע בעיר" in titles


def test_a_catalogue_page_gives_one_undated_course_each() -> None:
    from partylinks.scrapers.fournature import undated_catalogue

    html = (FIXTURES / "teva-city.html").read_text(encoding="utf-8")  # /events/teva-city/ (24.11.2026)
    courses = undated_catalogue(
        Page("https://www.4nature.org.il/events/teva-city/", html, date(2026, 11, 24)), SETTINGS
    )
    assert [c.title for c in courses] == [
        "חיות ואנשים בעיר הגדולה",
        "ציפורי העיר והעצים המופלאים",
        "חתולים, בני אדם וחיות אחרות",
        'פינוי והצלת בע"ח לפני הריסה',
        'גינון ושצ"פים ידידותיים לטבע',
        "תכנון נוף עירוני ידידותי לטבע",
    ]  # the page's opening "להרשמה" (no text) and the full course (no button) are not courses here
    first = courses[0]
    assert first.start_at is None and "קורס" in first.source_genres
    assert str(first.url) == "https://www.4nature.org.il/events/teva-city/#form"  # the page's order form
    assert first.venue_name is None and first.images  # booked on request: no place; its own picture
    assert len({c.extra["page"] for c in courses}) == 6  # one form for all, still six events


def test_an_offer_links_to_its_page_sign_up_form() -> None:
    from partylinks.scrapers.fournature import undated

    course_page = '<a href="#content">דילוג לתוכן</a><p>מרכז הקיימות</p><a href="#form">להרשמה</a>'
    others = {"https://www.4nature.org.il/events/rescue-course/": course_page}
    home = {
        e.title: e for e in undated(Page("https://www.4nature.org.il/", HOME, date(2026, 11, 18)), others, SETTINGS)
    }
    course = home["קורס הכרות, שמירה והצלת בעלי חיים עירוניים"]
    assert str(course.url) == "https://www.4nature.org.il/events/rescue-course/#form"  # straight to the form
    assert course.extra["purchase_url"] == "https://www.4nature.org.il/events/rescue-course/#form"
    assert "[להרשמה](https://www.4nature.org.il/events/rescue-course/#form)" in (course.description or "")
