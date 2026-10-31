from decimal import Decimal

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.reading3 import Reading3Settings, parse_listing, parse_show

SETTINGS = Reading3Settings(
    cron="18 9,21 * * *", site_url="https://www.reading3.co.il", venue_name="רידינג 3",
    address="יוסף יקותיאלי 6", city="תל אביב-יפו", latitude=32.1018, longitude=34.7761,
)  # fmt: skip

LISTING = """
<a class="top-shows-banner" href="../../../shows/a/view/?ContentID=4591" style="background-image: url('/Warehouse/content/pics/pic_4591_B.jpeg')"></a>
<a class="purchaseBtn" href="../../../shows/a/view/?ContentID=4591">לרכישה</a>
<a href="../../../shows/a/view/?ContentID=4606">עמרי סבח</a>
<a href="../../../home/a/main/">דף הבית</a>
"""

SHOW = """
<div class="EventTitle" style="display:none;"><h1>אורפנד לנד</h1></div>
<div class="EventBanner" style="display:none; background-image:url('/Warehouse/content/pics/pic_4591_d.jpg')"></div>
<table id="EventDetailsTable">
<tr><th>תאריך</th><th>פתיחת דלתות</th><th>תחילת מופע</th><th>סוג מופע</th><th>מחיר</th><th></th></tr>
<tr><td>9/12/2026</td><td>19:30</td><td>21:00</td><td>עמידה</td><td>החל מ-124 ש"ח</td>
<td><a href="https://shimon.kartisim.co.il/announce/ol?_gl=1*abc" target="_blank">לרכישת כרטיס</a></td></tr>
</table>
<div class="infoWrapper"><h3>אורפנד לנד</h3><p>ערב מטאל: 30 שנה לאלבום של הלהקה.</p></div>
"""


def test_listing_keeps_each_show_once_with_its_artwork() -> None:
    assert parse_listing(LISTING, SETTINGS.site_url) == {
        "4591": "https://www.reading3.co.il/Warehouse/content/pics/pic_4591_B.jpeg",
        "4606": "",
    }


def test_show_page_gives_time_place_price_and_seller() -> None:
    url = "https://www.reading3.co.il/he/shows/a/view/?ContentID=4591"
    [event] = parse_show(SHOW, "4591", url, "https://www.reading3.co.il/x_B.jpeg", SETTINGS, "Asia/Jerusalem")
    assert event.title == "אורפנד לנד" and event.source_event_id == "4591"
    assert event.start_at.isoformat() == "2026-12-09T21:00:00+02:00" and event.time_known
    assert (event.latitude, event.longitude, event.venue_name) == (32.1018, 34.7761, "רידינג 3")
    assert event.min_price == Decimal(124)
    assert event.extra["purchase_url"] == "https://shimon.kartisim.co.il/announce/ol"  # analytics query dropped
    assert event.extra["open_doors"] == "19:30"
    assert event.images[0] == "https://www.reading3.co.il/Warehouse/content/pics/pic_4591_d.jpg"
    assert event.description and event.description.startswith("ערב מטאל")
    settings = get_settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched and "live_show" in enriched.genres


def test_rows_without_a_date_are_skipped() -> None:
    page = SHOW.replace("9/12/2026", "")
    assert parse_show(page, "4591", "u", "", SETTINGS, "Asia/Jerusalem") == []
