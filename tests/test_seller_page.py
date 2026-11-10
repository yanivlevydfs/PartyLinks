"""Prices on a seller's event page behind a city calendar's ticket button (markup of Nov 2026 pages)."""

from __future__ import annotations

from decimal import Decimal

from partylinks.models import TierStatus
from partylinks.scrapers.munievents import ticket_link
from partylinks.scrapers.seller_page import seller_tiers

# events.kehilatayim.org.il/Kehilatayim_events/291756 and /287530 (Coing white label)
COING_ROW = (
    '<div class="info-col"><div class="info-col-label"><svg class="svg-sprite-text info-col-icon"><use'
    ' xlink:href="#icon-payment"></use></svg><span>מחיר</span></div><div class="info-col-value"><div>{}</div></div></div>'
)
# event.matnasbinyamin.co.il/115358 (Tickchak white label)
JSONLD = """<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Event", "name": "נופש",
"offers": [{"@type": "Offer", "name": " נופש לזוג ", "price": "4390.00", "priceCurrency": "ILS", "availability": "in_stock"},
{"@type": "Offer", "name": "יחיד", "price": "2195.00", "priceCurrency": "ILS", "availability": "out_of_stock"}]}</script>"""


def test_coing_price_row() -> None:
    tiers = seller_tiers(COING_ROW.format('190 ש"ח(תושב 165 ש"ח)'))
    assert sorted(t.price for t in tiers) == [Decimal(165), Decimal(190)]
    free = seller_tiers(COING_ROW.format("ללא עלות, בהרשמה מראש"))
    assert [(t.price, t.is_free) for t in free] == [(Decimal(0), True)]


def test_jsonld_offers() -> None:
    tiers = seller_tiers(JSONLD)
    assert [(t.name, t.price, t.status) for t in tiers] == [
        ("נופש לזוג", Decimal("4390.00"), TierStatus.ON_SALE),
        ("יחיד", Decimal("2195.00"), TierStatus.SOLD_OUT),
    ]


def test_mevalim_buy_button() -> None:
    page = '<button type="button"><span class="leading-none">בחירת כרטיסים ב-₪35</span></button>'
    assert [t.price for t in seller_tiers(page)] == [Decimal(35)]
    assert seller_tiers("<p>מחיר: 35 ₪ לכל הסדרה</p><a>אירועים נוספים</a>") == []


def test_ticket_link_of_a_city_event_page() -> None:
    # givatayim.muni.il/events/10833/: no tickets row, a "הזמנת כרטיסים" button to Kehilatayim.
    page = """<main><a href="https://waze.com/ul?ll=32.07,34.81" class="btn">Waze</a>
    <div class="mt-4"><a href="https://events.kehilatayim.org.il/Kehilatayim_events/287530" class="btn btn-primary btn-lg"
    target="_Blank">הזמנת כרטיסים</a></div></main>"""
    base = "https://www.givatayim.muni.il"
    assert ticket_link(page, base, []) == "https://events.kehilatayim.org.il/Kehilatayim_events/287530"
    assert ticket_link(page, base, ["kehilatayim.org.il"]) is None
    sells = '<div class="event-item-row tickets"><div class="event-item-value">40 ₪</div></div>' + page
    assert ticket_link(sells, base, []) is None


def test_jsonld_of_the_page_event_only() -> None:
    # tickchak.co.il/113272 lists 30 other events' JSON-LD beside its own; the first is another one.
    other = '{"@type": "Event", "name": "יריד", "offers": {"@type": "Offer", "url": "https://tickchak.co.il/113185", "price": "50"}}'
    mine = '{"@type": "Event", "name": "התוועדות", "offers": {"@type": "Offer", "url": "https://tickchak.co.il/113272/form", "price": "30"}}'
    page = "".join(f'<script type="application/ld+json">{x}</script>' for x in (other, mine))
    assert [t.price for t in seller_tiers(page, "https://tickchak.co.il/113272")] == [Decimal(30)]
    assert seller_tiers(page, "https://tickchak.co.il/999") == []  # neither is the page's: no guess
    # A listing page ("/shows") whose one JSON-LD event is some other show: not this event's price.
    one = f'<script type="application/ld+json">{other}</script>'
    assert seller_tiers(one, "https://www.comedyquiz.net/shows") == []
