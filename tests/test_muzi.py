"""Muzi: sitemap listing and event-page parsing on trimmed real pages (no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from partylinks.scrapers.muzi import artwork, event_urls, parse_event, sitemap_urls

FIXTURES = Path(__file__).parent / "fixtures" / "muzi"
TZ = "Asia/Jerusalem"
URL = "https://muzi.co.il/event/jazz-eilat/"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_sitemaps() -> None:
    index = "<sitemap><loc>https://muzi.co.il/post-sitemap.xml</loc></sitemap><sitemap><loc>https://muzi.co.il/event-sitemap2.xml</loc></sitemap>"
    assert sitemap_urls(index)[1] == "https://muzi.co.il/event-sitemap2.xml"
    sitemap = (
        "<url><loc>https://muzi.co.il/event/</loc></url>"
        "<url><loc>https://muzi.co.il/event/%d7%a1%d7%95%d7%9c-2/</loc></url>"
        "<url><loc>https://muzi.co.il/event/a&amp;b/</loc></url>"
    )
    assert event_urls(sitemap, "/event/") == [
        "https://muzi.co.il/event/%d7%a1%d7%95%d7%9c-2/",
        "https://muzi.co.il/event/a&b/",
    ]


def test_paid_event() -> None:
    event = parse_event(_page("event_paid.html"), URL, TZ, today=date(2026, 10, 23))
    assert event is not None
    assert event.source == "muzi"
    assert event.source_event_id == "109458"
    assert event.title.startswith("פסטיבל ג’אז בים האדום אילת")
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-14T14:15:00+02:00"
    assert (event.venue_name, event.city, event.country) == ("PORT ARENA", "אילת", None)
    assert event.artists == ["בלקן ביט בוקס"]
    assert event.source_genres == ["jazz"]
    assert event.min_price == event.max_price == Decimal(229)
    # The event's poster first, then the artist photo of the details as its full upload.
    assert event.images == [
        "https://muzi.co.il/wp-content/uploads/2025/02/67b1a0b24ed57.webp",
        "https://muzi.co.il/wp-content/uploads/2024/01/65b3a31a335e9.jpg",
    ]
    assert event.extra == {
        "purchase_url": "https://www.ticketmaster.co.il/performance/MEP29/001/ALL/iw",
        "doors_open": "13:30",
    }


def test_free_and_range_prices() -> None:
    free = parse_event(_page("event_free.html"), URL, TZ)
    assert free is not None and free.min_price == Decimal(0) and free.tickets[0].is_free
    assert free.start_at is not None and free.start_at.isoformat() == "2026-10-28T12:30:00+02:00"
    ranged = parse_event(_page("event_range.html"), URL, TZ)
    assert ranged is not None and (ranged.min_price, ranged.max_price) == (Decimal(50), Decimal(60))
    assert ranged.source_genres == ["electronic"]


def test_online_and_promo_posts_skipped() -> None:
    """event_type-online (the site's promo / deal posts) and events_by_region-live-online (online courses)."""
    skip = ["event_type-online", "events_by_region-live-online"]
    page = _page("event_range.html")
    assert parse_event(page, URL, TZ, skip_classes=skip) is not None
    for tag in skip:
        tagged = page.replace("event_type-standing", tag)
        assert parse_event(tagged, URL, TZ, skip_classes=skip) is None


def test_past_event_skipped() -> None:
    assert parse_event(_page("event_free.html"), URL, TZ, today=date(2026, 10, 29)) is None


def test_event_block_with_raw_newlines() -> None:
    """The event JSON-LD keeps raw newlines in its description (invalid strict JSON)."""
    event = parse_event(_page("event_raw_newlines.html"), URL, TZ)
    assert event is not None and event.title == "Bayz Rooftop The Last Dance"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-10-24T14:00:00+03:00"
    assert event.extra["purchase_url"].startswith("https://go-out.co/event/1792588673056")
    assert event.artists == ["GENISH"]


def test_unescaped_backslash_and_quotes_in_description() -> None:
    page = _page("event_raw_newlines.html").replace('"description": "Genish |', r'"description": "Genish \ "Orkey" |')
    event = parse_event(page, URL, TZ)
    assert event is not None and event.description and event.description.startswith(r'Genish \ "Orkey"')


def test_unescaped_quotes_outside_the_description() -> None:
    """A place written with a plain ASCII gershayim ("ויצ"ו חיפה") broke the whole block: the show was lost."""
    page = _page("event_raw_newlines.html").replace(
        '"@type": "Place",', '"@type": "Place", "alternateName": "ויצ"ו חיפה",'
    )
    assert '"ויצ"ו' in page
    event = parse_event(page, URL, TZ)
    assert event is not None and event.title == "Bayz Rooftop The Last Dance"


def test_placeholder_place_takes_the_visible_where_row() -> None:
    page = _page("event_free.html")
    event = parse_event(page, URL, TZ)
    assert event is not None and event.venue_name
    placeholder = page.replace(f'"name": "{event.venue_name}"', '"name": "Hall"', 1)
    assert placeholder != page
    fixed = parse_event(placeholder, URL, TZ, placeholders=["Hall"])
    # The page's own "איפה?" row: "<p>קמפוס הטכניון <span>חיפה</span></p>".
    assert fixed is not None and (fixed.venue_name, fixed.city) == ("קמפוס הטכניון", "חיפה")


_OG = '<meta property="og:image" content="https://muzi.co.il/wp-content/uploads/{}" />\n'
_ICON_META = (
    '<meta property="og:image:width" content="512" />\n<meta property="og:image:height" content="512" />\n'
    '<meta property="og:image:type" content="image/png" />\n'
)
_PIC = '<img class="concert_pic" src="https://muzi.co.il/wp-content/{}"  alt="x" title="x">\n'


def test_theme_stand_in_means_no_picture() -> None:
    """Moshe Peretz at Menora (Nov 2026): no photo of its own, so og:image was the genre's drum icon, which
    eventer, tickchak, ogo and others then borrowed by title. Every snippet is the real page's."""
    page = (
        _OG.format("2026/07/6a9d837a2ed1c.png")
        + _ICON_META
        + _PIC.format("themes/muzi-theme/assets/images/s_artist-100x100.webp")
    )
    assert artwork(page) == []
    # The site icon of other pages without a photo, and the family icon (1024 WebP): no picture either.
    assert (
        artwork(
            _OG.format("2022/06/cropped-muzi-favicon.png")
            + _PIC.format("themes/muzi-theme/assets/images/s_artist-100x100.webp")
        )
        == []
    )
    assert (
        artwork(
            _OG.format("2024/07/670f825135ba1.webp")
            + _PIC.format("themes/muzi-theme/assets/images/s_artist-100x100.webp")
        )
        == []
    )


def test_artist_photo_wins_over_a_genre_icon() -> None:
    name = "דני רובס - צילום גבריאל בהרליה - 2022"
    page = _OG.format("2026/07/6a9d83e464bed.png") + _ICON_META + _PIC.format(f"uploads/2023/10/{name}-100x100.jpg")
    assert artwork(page) == [f"https://muzi.co.il/wp-content/uploads/2023/10/{quote(name)}.jpg"]


def test_og_image_of_the_same_upload_is_kept() -> None:
    """og:image is the photo's big copy ("-scaled", or an edit "-e<stamp>" in another month's folder)."""
    scaled = _OG.format("2024/11/672b72b8d9346-scaled.jpg") + _PIC.format("uploads/2024/11/672b72b8d9346-100x100.jpg")
    assert artwork(scaled) == ["https://muzi.co.il/wp-content/uploads/2024/11/672b72b8d9346-scaled.jpg"]
    edited = _OG.format("2022/05/נינט2-ליאור-כתר.jpg") + _PIC.format(
        "uploads/2022/06/נינט2-ליאור-כתר-e1654170851666-100x100.jpg"
    )
    assert artwork(edited) == [
        "https://muzi.co.il/wp-content/uploads/2022/05/%D7%A0%D7%99%D7%A0%D7%982-%D7%9C%D7%99%D7%90%D7%95%D7%A8-%D7%9B%D7%AA%D7%A8.jpg"
    ]
