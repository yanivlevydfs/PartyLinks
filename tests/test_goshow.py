"""GoShow: home-page cards and a date's page, on trimmed real pages (no network)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.goshow import GoShowSettings, add_page, card_event, date_event, parse_listing, parse_more_dates

FIXTURES = Path(__file__).parent / "fixtures" / "goshow"
TZ = "Asia/Jerusalem"
SETTINGS = GoShowSettings(cron="21 7,19 * * *")


def _cards() -> list[dict]:
    return parse_listing((FIXTURES / "home.html").read_text(encoding="utf-8"), TZ)


def test_listing_reads_each_date_once() -> None:
    cards = _cards()
    assert [c["date_id"] for c in cards] == ["55398", "57539"]  # the repeated card is read once
    first = cards[0]
    assert (first["show_id"], first["title"]) == ("14087", "נופר סויסה במופע סטנדאפ")
    assert (first["venue"], first["city"]) == ("בית ציוני אמריקה", "תל אביב-יפו")
    assert first["start"].isoformat() == "2026-09-18T21:00:00+03:00"
    # A venue with its street: "בר גיורא, אבן גבירול 30 , תל אביב-יפו" -> the city is the last part.
    assert cards[1]["city"] == "תל אביב-יפו" and cards[1]["venue"].startswith("בר גיורא")


def test_date_page_gives_title_artwork_prices_and_producer() -> None:
    event = card_event(_cards()[0], SETTINGS)
    assert str(event.url) == "https://www.goshow.co.il/show/14087/55398/"
    assert event.address == "בית ציוני אמריקה, תל אביב-יפו"
    add_page(event, (FIXTURES / "show.html").read_text(encoding="utf-8"), SETTINGS.site_url)
    assert event.title == "נופר סויסה במופע סטנדאפ"
    assert event.images == ["https://www.goshow.co.il/manager/userfiles/gallery/גלריה517.png"]
    assert event.description and "סטנדאפיסטית" in event.description
    # The open tier only: the student and soldier rows are commented out in the page.
    assert [(t.name, t.price) for t in event.tickets] == [("רגיל", Decimal(73))]
    assert (event.min_price, event.max_price) == (Decimal(73), Decimal(73))
    assert event.extra["producer"] == "קומדי בר"
    # The venue's Google Maps embed: "!2d<lon>!3d<lat>".
    assert (round(event.latitude, 5), round(event.longitude, 5)) == (32.0743, 34.78427)
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres


def test_heading_wins_over_an_unescaped_og_title() -> None:
    """og:title of 'ד"ר שי בידרמן' reads as 'ד': the page's heading has the full title."""
    event = card_event(_cards()[1], SETTINGS)
    page = '<meta property="og:title" content="ד"ר שי"><div class="showDescTitle"><h3>ד"ר שי בידרמן</h3></div>'
    add_page(event, page, SETTINGS.site_url)
    assert event.title == 'ד"ר שי בידרמן'


# The producer's list on a date's page, trimmed from the live page (one more date of show 14087 and
# one of another show); the commented-out place line is what the page has too.
MORE = """<div class="moreShows"><ul>
<li><div class="liShowDesc"><p class="descDate">26/11/2026</p><p class="descTime">22:00</p>
<!--<p class="descPlace">מקום: מועדון תאטרון גבעתיים, </p>-->
<p class="descPlace"><b>אירוע:</b> נופר סויסה במופע סטנדאפ<br><b>מקום:</b> מועדון תאטרון גבעתיים , גבעתיים</p>
<p class="descPrice">73 ₪</p></div>
<div class="liShowGo"><a class="more-shows" href="/event/14087/57389/נופר-סויסה&iframe=&cc="></a></div></li>
<li><div class="liShowDesc"><p class="descDate">26/11/2026</p><p class="descTime">23:30</p>
<p class="descPlace"><b>אירוע:</b> נותנים בראש<br><b>מקום:</b> קומדי בר , תל אביב-יפו</p>
<p class="descPrice">63 ₪</p></div>
<div class="liShowGo"><a class="more-shows" href="/event/16000/57400/נותנים-בראש&iframe=&cc="></a></div></li>
</ul></div>"""


def test_old_map_link_gives_no_point() -> None:
    """An old "maps?q=<name>" embed names the place only; its sll= is a fixed Tel Aviv point."""
    event = card_event(_cards()[0], SETTINGS)
    page = (
        '<iframe src="https://maps.google.co.il/maps?f=q&q=%D7%92%D7%91%D7%A2%D7%AA&sll=32.065022,34.77349"></iframe>'
    )
    add_page(event, page, SETTINGS.site_url)
    assert (event.latitude, event.longitude) == (None, None)


def test_producer_list_gives_the_other_dates() -> None:
    same, other = parse_more_dates(MORE, TZ)
    assert (same["show_id"], same["date_id"], same["title"]) == ("14087", "57389", "נופר סויסה במופע סטנדאפ")
    assert (same["venue"], same["city"], same["price"]) == ("מועדון תאטרון גבעתיים", "גבעתיים", Decimal(73))
    assert same["start"].isoformat() == "2026-11-26T22:00:00+02:00"
    assert (other["date_id"], other["venue"], other["price"]) == ("57400", "קומדי בר", Decimal(63))


def test_other_date_reuses_its_show_page_and_keeps_its_own_date_place_and_price() -> None:
    genres = {"14087": {"standup"}}
    show = card_event(_cards()[0], SETTINGS, genres)
    add_page(show, (FIXTURES / "show.html").read_text(encoding="utf-8"), SETTINGS.site_url)
    same, other = (date_event(c, SETTINGS, genres, {"14087": show}) for c in parse_more_dates(MORE, TZ))
    assert (same.source_event_id, str(same.url)) == ("57389", "https://www.goshow.co.il/show/14087/57389/")
    assert (same.venue_name, same.city) == ("מועדון תאטרון גבעתיים", "גבעתיים")
    assert same.start_at.isoformat() == "2026-11-26T22:00:00+02:00"
    assert same.images == show.images and same.description == show.description and same.tickets == show.tickets
    assert same.extra["producer"] == "קומדי בר" and same.source_genres == ["standup"]
    assert same.latitude is None  # another venue than the show page's map: geocoding places it
    # A show whose page was not read: the list's own title, place and price, no categories.
    assert (other.title, other.min_price, other.tickets, other.source_genres) == ("נותנים בראש", Decimal(63), [], [])


def test_category_card_is_absolute_and_maps_to_a_genre() -> None:
    page = (
        '<li class="carousel_row"><div class="closeTitle"><p>שלמה בייבי</p></div>'
        '<a href="https://www.goshow.co.il/event/14420/56817/x" class="closeShowTitle">חוצות תרבות, אשקלון</a>'
        '<a href="https://www.goshow.co.il/event/14420/56817/x" class="closeShowDate">יום רביעי 14-10-26 בשעה 21:00</a></li>'
    )
    [card] = parse_listing(page, TZ)
    assert (card["show_id"], card["date_id"], card["city"]) == ("14420", "56817", "אשקלון")
    event = card_event(card, SETTINGS, {"14420": {"standup"}})
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres
