from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ticketeams import Terms, parse_product, parse_terms

PAGE = """<html><head>
<meta property="og:image" content="https://ticketeams.co.il/wp-content/uploads/2024/09/רקע.png" />
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"Organization","logo":{"@type":"ImageObject","url":"https://ticketeams.co.il/wp-content/uploads/2024/09/\\u05e8\\u05e7\\u05e2.png"}},
{"@type":"Product","name":"x","offers":{"@type":"AggregateOffer","lowPrice":"145","highPrice":"394",
 "priceCurrency":"GBP","availability":"http://schema.org/OutOfStock"}}]}</script></head><body>
<h3 class="elementor-icon-box-title"><span > פייסבוק </span></h3>
<h3 class="elementor-icon-box-title"><span > אולד טראפורד </span></h3>
<div class="jet-listing-dynamic-field__content" >ינואר 7, 2027</div>
<h3 class="elementor-icon-box-title"><span > 20:00 </span></h3>
</body></html>"""

TERMS = Terms({
    "product_cat": parse_terms([{"id": 42, "name": "כדורגל", "slug": "football", "parent": 0},
                                {"id": 45, "name": "ליגה אנגלית", "slug": "premier-league", "parent": 42}]),
    "stadiums": parse_terms([{"id": 284, "name": "אולד טראפורד", "slug": "284"}]),
    "cities": parse_terms([{"id": 7, "name": "מנצ'סטר", "slug": "manchester"}]),
    "countrys": parse_terms([{"id": 41, "name": "אנגליה", "slug": "england"}]),
})  # fmt: skip
PRODUCT = {"id": 3567, "link": "https://ticketeams.co.il/product/man-utd-vs-newcastle/",
           "title": {"rendered": "מנצ&#039;סטר יונייטד נגד ניוקאסל"}, "product_cat": [45], "stadiums": [284],
           "cities": [7], "countrys": [41]}  # fmt: skip


def test_product_page() -> None:
    event = parse_product(PAGE, PRODUCT, TERMS, "Asia/Jerusalem")
    assert event is not None
    assert event.title == "מנצ'סטר יונייטד נגד ניוקאסל"
    assert event.start_at is not None and event.start_at.isoformat() == "2027-01-07T20:00:00+00:00"  # London time
    assert (event.venue_name, event.city, event.country) == ("אולד טראפורד", "מנצ'סטר", "GB")
    assert (event.currency, event.min_price, event.max_price, event.is_sold_out) == ("GBP", 145, 394, True)
    assert event.images == []  # no picture of its own: og:image is the site's background, not the event's
    own = PAGE.replace('{"@type":"Product","name":"x",', '{"@type":"Product","name":"x","image":[{"@type":"ImageObject",'
                       '"url":"https://ticketeams.co.il/wp-content/uploads/2026/10/old-trafford-map.jpg"}],')
    pictured = parse_product(own, PRODUCT, TERMS, "Asia/Jerusalem")
    assert pictured is not None and pictured.images == ["https://ticketeams.co.il/wp-content/uploads/2026/10/old-trafford-map.jpg"]
    assert event.source_genres == ["ליגה אנגלית", "כדורגל", "ספורט"]
    assert "sport" in Enricher(Settings().enrichment, "Asia/Jerusalem")._detect_genres(event)
    assert parse_product("<div></div>", PRODUCT, TERMS, "UTC") is None  # no date: not an event
