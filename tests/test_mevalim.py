"""Mevalim: every /<slug>/ page of the index pages, and carousel items next to table rows."""

from __future__ import annotations

from decimal import Decimal

from partylinks.scrapers import mevalim

TZ = "Asia/Jerusalem"
SITE = "https://www.mevalim.co.il"

# Shape of a home-page carousel item (thumbnail shortcode), as the load-more call returns it.
CAROUSEL = """
<a class="rgbcode_table_shortcode_thumbnail_item gtm_view_list_item"
   href="https://tickets.mevalim.co.il/event/mPAlVaSHTddrJad?referrer=x&#038;idx=8"
   data-mvlm-brand="Adiel" data-mvlm-event-category="הרצאות" data-mvlm-table-id="187892"
   data-mvlm-event-id="133268" data-mvlm-event-name="איתי אנגל" data-mvlm-event-date="20.01.27 20:30"
   data-mvlm-event-domain="tickets.mevalim.co.il">
  <div class="rgbcode_table_shortcode_thumbnail_thumbnail">
    <div class="mevalim_badge"><span class="mevalim_badge__text"><s>65₪</s> 45₪</span></div>
  </div>
  <div class="rgbcode_table_shortcode_thumbnail_date_wrap">
    <div class="rgbcode_table_shortcode_thumbnail_city">בית שמש</div>
    <div class="rgbcode_table_shortcode_thumbnail_hour">20:30</div>
  </div>
  <div class="rgbcode_table_shortcode_thumbnail_event_name">איתי אנגל</div>
</a>
"""


def test_linked_pages_are_the_single_segment_pages() -> None:
    page = (
        f'<a href="{SITE}/rehovot/">רחובות</a><a href="{SITE}/stand-up/">סטנדאפ</a>'
        f'<a href="{SITE}/tel-aviv/%d7%a6%d7%95%d7%95%d7%aa%d7%90/">צוותא</a>'  # a hall: under its city
        f'<a href="{SITE}/wp-json/">api</a><a href="{SITE}/rehovot/">again</a><a href="https://other.example/x/">x</a>'
    )
    assert mevalim.linked_pages(page, SITE) == ["/rehovot/", "/stand-up/"]


def test_carousel_item_is_an_event() -> None:
    (item,) = mevalim.parse_rows(CAROUSEL)
    event = mevalim.parse_row(item, SITE + "/", TZ)
    assert event.source_event_id == "133268"
    assert event.title == "איתי אנגל"
    assert event.city == "בית שמש"
    assert event.venue_name is None
    assert str(event.url) == "https://tickets.mevalim.co.il/event/mPAlVaSHTddrJad"
    assert event.min_price == Decimal(45)  # the badge's deal price, not the struck-out one
    assert event.event_types == ["הרצאות"]
    assert event.start_at is not None and event.start_at.year == 2027


def test_table_rows_still_parse_next_to_carousels() -> None:
    from pathlib import Path

    rows_html = (Path(__file__).parent / "fixtures" / "mevalim_rows.html").read_text(encoding="utf-8")
    rows = mevalim.parse_rows(rows_html + CAROUSEL)
    assert sorted(str(r.tag) for r in rows) == ["a", "tr", "tr"]


def test_past_rows_are_site_typos() -> None:
    (item,) = mevalim.parse_rows(CAROUSEL.replace("20.01.27 20:30", "19.02.00 21:30"))
    assert mevalim.is_past(mevalim.parse_row(item, SITE + "/", TZ))
    (item,) = mevalim.parse_rows(CAROUSEL)
    assert not mevalim.is_past(mevalim.parse_row(item, SITE + "/", TZ))
