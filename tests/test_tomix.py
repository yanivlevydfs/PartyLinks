"""toMix: category grids, show pages, their box offices (Eventer / BiggerPicture) and the events built from them."""

from __future__ import annotations

from datetime import date

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.tomix import (
    Hall,
    Product,
    TomixSettings,
    eventer_event,
    page_url,
    parse_category_page,
    parse_checkout,
    parse_product,
    product_key,
    product_links,
    shop_event,
    windows,
)

TZ = "Asia/Jerusalem"
CFG = TomixSettings(
    cron="0 0 * * *",
    venues={"tox - ראשון לציון": Hall(venue_name="tox", city="ראשון לציון", latitude=31.9876287, longitude=34.763967)},
    places={"כפר הנוקדים": Hall(venue_name="כפר הנוקדים", city="ערד")},
)

CATEGORY = """<html><head><meta property="og:title" content="במה ומופעים" /></head><body>
<div class="carousel"><div class="boxitem wl_mix_go" data-url="https://www.to-mix.co.il/product/promo-only/"></div></div>
<div class="row fwrap" id="wl_ajax_container"><div class="page-number" data-page="1"></div>
<div class="col-sm-12"><div class="boxitem wl_mix_go boxitem-promo" id="1229357"
 data-url="https://www.to-mix.co.il/product/nisuyim-gariniyim-comedy/"></div></div>
<div class="col-sm-12"><div class="boxitem wl_mix_go" id="1"
 data-url="https://www.to-mix.co.il/product/%d7%a1%d7%99%d7%95%d7%a8/?swcfpc=1"></div></div>
<input type="hidden" id="wl_load_more_mix" data-page="1" data-has-more-mix="1"></div></body></html>"""

SHOW = """<html><head><meta property="og:title" content="להקת מחול בת־שבע | אנאפאזה" />
<meta property="og:image" content="https://www.to-mix.co.il/wp-content/uploads/2026/01/ללא-שם.jpg" /></head><body>
<div class="ptitlebox flex vbtm" id="perfect_for"><h2>להקת מחול בת־שבע | אנאפאזה</h2></div>
<div class="mix-maininfo-content"><p>For English</p><p>אנאפאזה, יצירתו של אוהד נהרין.</p></div>
<div class="btnholder"><a href="https://www.to-mix.co.il/product/anafaza2026-bitan1/?eventbuzz=true"
 class="btn mix-add-to-cart eventbuzz-button">לרכישה</a></div></body></html>"""

VOUCHER = """<html><body><div id="perfect_for"><h2>טיפול ספא ראש יפני</h2></div><div class="btnholder">
<button class="btn multimix_add_to_cart" data-whom="me">לרכישה</button></div></body></html>"""

CHECKOUT = """<!-- IFRAME CHECKOUT START --><table><tbody>
<tr><td>ביתן 1</td><td>12.12.26</td><td>שבת</td><td>21:00</td>
<td><button onclick="openPopup('https://www.eventer.co.il/rpz3f')">לרכישה</button></td></tr></tbody></table>
<iframe src="https://www.eventer.co.il/user/mixcard?tag=ozeret&colorScheme=%23FFFFFF"></iframe>
<script src="https://www.eventer.co.il/js/vendor/iframeResizer.min.js"></script>
<script>window.location.replace("https://tickets.to-mix.co.il/site/886?saleChannelCode=web&code=886-80&returnUrl=x");</script>
<a href="https://app.to-mix.co.il/event/adminclientredirect/?emcode=1792589341083">x</a>
<!-- IFRAME CHECKOUT END --><a href="https://www.eventer.co.il/outside">not the box office</a>"""

EVENTER = {
    "_id": "6a54af657b7d8e64b528ef99",
    "status": 1,
    "name": "בילי שוורץ",
    "linkName": "l5dff",
    "tags": ["billis"],
    "location": {"latitude": 31.9627552, "longitude": 34.8006286, "timezone": TZ},
    "locationDescription": "היכל התרבות ראשון לציון",
    "schedule": {"start": "2026-12-24 21:00", "end": "2026-12-24 23:30"},
    "eventCategories": {"topEventCategoryString": "Concerts"},
    "ticketTypes": [{"_id": "t1", "name": "כרטיס כניסה", "price": 189, "remaining": -1}],
}

PRODUCT = Product(
    url="https://www.to-mix.co.il/product/billispele-ph26/",
    title="בילי שוורץ",
    image="https://www.to-mix.co.il/wp-content/uploads/2026/11/billi.jpg",
    description="מופע להקה",
    labels=["במה ומופעים"],
    artists=["בילי שוורץ"],
)

ROW = {
    "eventId": 80,
    "eventMasterId": 50,
    "dateTimeOfEvent": "2027-02-13T21:30:00",
    "venueName": "tox - ראשון לציון",
    "lengthInMinutes": 120,
    "eventMasterName": "דויד ברוזה - אל תשאלי אם אני אוהב",
}
MASTER = {"eventMasterId": 50, "displayName": "דויד ברוזה - אל תשאלי אם אני אוהב", "code": "1794929028203"}


def test_category_page_grid_only() -> None:
    name, products, more = parse_category_page(CATEGORY)
    assert name == "במה ומופעים"
    assert products == [
        "https://www.to-mix.co.il/product/nisuyim-gariniyim-comedy/",
        "https://www.to-mix.co.il/product/סיור/",
    ]
    assert more
    assert not parse_category_page(CATEGORY.replace('data-has-more-mix="1"', 'data-has-more-mix="0"'))[2]


def test_product_keys_and_links() -> None:
    key = product_key("https://www.to-mix.co.il/product/%d7%a1%d7%99%d7%95%d7%a8?swcfpc=1")
    assert key == "https://www.to-mix.co.il/product/סיור/"
    assert page_url(key) == "https://www.to-mix.co.il/product/%D7%A1%D7%99%D7%95%D7%A8/"
    html = '<a href="https://www.to-mix.co.il/product/a/">a</a> <a href="https://www.to-mix.co.il/product/a/?x=1">'
    assert product_links(html) == ["https://www.to-mix.co.il/product/a/"]


def test_show_and_voucher() -> None:
    product = parse_product(SHOW, "https://www.to-mix.co.il/product/anafaza2026-bitan1/")
    assert product is not None
    assert product.title == "להקת מחול בת־שבע | אנאפאזה"
    assert product.description == "אנאפאזה, יצירתו של אוהד נהרין."
    assert product.image and product.image.startswith("https://www.to-mix.co.il/wp-content/uploads/2026/01/")
    assert parse_product(VOUCHER, "https://www.to-mix.co.il/product/head-spa/") is None


def test_checkout_kinds() -> None:
    checkout = parse_checkout(CHECKOUT)
    assert checkout.eventer_links == ["rpz3f"]  # not the resizer script, not links outside the box office
    assert checkout.eventer_tags == [("mixcard", "ozeret")]
    assert checkout.shop_events == [80]
    assert checkout.master_codes == ["1792589341083"]
    assert parse_checkout("<p>no box office</p>").eventer_links == []


def test_eventer_event_takes_the_product() -> None:
    tickets = {"ticketTypes": EVENTER["ticketTypes"]}
    event = eventer_event({"event": EVENTER}, tickets, PRODUCT, CFG, TZ)
    assert event.source == "tomix"
    assert event.source_event_id == "eventer-6a54af657b7d8e64b528ef99"
    assert str(event.url) == PRODUCT.url
    assert event.extra["purchase_url"] == "https://www.eventer.co.il/l5dff"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-24T21:00:00+02:00"
    assert (event.latitude, event.longitude) == (31.9627552, 34.8006286)
    assert event.source_genres == ["במה ומופעים"]
    assert event.lineup == ["בילי שוורץ"]
    assert str(event.images[0]) == PRODUCT.image
    assert event.min_price == 189
    settings = Settings()
    assert "live_show" in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)


def test_eventer_event_without_product_or_town() -> None:
    raw = EVENTER | {"locationDescription": "כפר הנוקדים", "location": {"latitude": 31.3, "longitude": 35.2}}
    event = eventer_event({"event": raw}, {}, None, CFG, TZ)
    assert str(event.url) == "https://www.eventer.co.il/l5dff"
    assert event.source_genres == ["במה ומופעים"]  # the default: a show
    assert (event.venue_name, event.city) == ("כפר הנוקדים", "ערד")


def test_shop_event() -> None:
    event = shop_event(ROW, MASTER, [], None, ("https://app.to-mix.co.il/event/x/", "https://assets/x.png"), CFG, TZ)
    assert event is not None
    assert event.source_event_id == "shop-886-80"
    assert str(event.url) == "https://app.to-mix.co.il/event/x/"
    assert event.title == "דויד ברוזה - אל תשאלי אם אני אוהב"
    assert event.start_at is not None and event.start_at.isoformat() == "2027-02-13T21:30:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2027-02-13T23:30:00+02:00"
    assert (event.venue_name, event.city, event.latitude) == ("tox", "ראשון לציון", 31.9876287)
    assert event.extra["purchase_url"] == "https://tickets.to-mix.co.il/site/886?saleChannelCode=web&code=886-80"
    with_product = shop_event(ROW, MASTER, [], PRODUCT, None, CFG, TZ)
    assert with_product is not None and str(with_product.url) == PRODUCT.url and with_product.title == "בילי שוורץ"
    unknown = shop_event(ROW | {"venueName": "אולם חדש"}, MASTER, [], None, None, CFG, TZ)
    assert unknown is not None and (unknown.venue_name, unknown.city) == ("אולם חדש", None)
    assert shop_event(ROW | {"dateTimeOfEvent": None}, MASTER, [], None, None, CFG, TZ) is None


def test_windows() -> None:
    days = [date(2026, 12, 1), date(2027, 1, 29), date(2027, 1, 30), date(2027, 5, 1)]
    assert windows(days, 59) == [
        (date(2026, 12, 1), date(2027, 1, 29)),
        (date(2027, 1, 30), date(2027, 1, 30)),
        (date(2027, 5, 1), date(2027, 5, 1)),
    ]
    assert windows([], 59) == []
