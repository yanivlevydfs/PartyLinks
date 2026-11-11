import datetime
import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers.showshappening import parse_event, show_id, ticket_tiers

PAGE = """<input type="hidden" class="showdate" value="Thursday 05-Nov-2026 19:30" />
<input type="hidden" id="EventVenueName" value="Follow the Sun Boutique Hostel" />
<input type="hidden" id="EventVenueAddress" value="Follow the Sun Boutique Hostel,Triq Telghet, San Giljan, Malta" />
<input type="hidden" id="EventVenueGoogleLocation" value="(35.9127531,14.4933634)" />
<input type="hidden" name="EventId" id="eventid" value="d1d106da-4656-451f-9f23-697f75d3b0dc" />"""


def test_venue_address_and_point_from_hidden_fields() -> None:
    day = datetime.date.today() + datetime.timedelta(days=7)
    listed = {"title": "Social Mixer", "path": "/english-cafe-malta/social-mixer", "country": "MT", "dates": [day],
              "image": None, "categories": [], "tags": []}  # fmt: skip
    event = parse_event(listed, PAGE, "https://www.showshappening.com", "Asia/Jerusalem")[0]
    assert event.venue_name == "Follow the Sun Boutique Hostel"
    assert event.address == "Follow the Sun Boutique Hostel,Triq Telghet, San Giljan, Malta"
    assert (event.latitude, event.longitude) == (35.9127531, 14.4933634)
    assert event.start_at is not None
    assert event.start_at.hour == 19 and str(event.start_at.tzinfo) == "Europe/Malta"


def test_venue_from_the_api_when_the_page_has_no_hidden_fields() -> None:
    # Newer pages are drawn by JavaScript: the venue comes from apicore's /events/<eventid>.
    day = datetime.date.today() + datetime.timedelta(days=7)
    listed = {"title": "Acid Night", "path": "/Acidulant/acid-night-1", "country": "MT", "dates": [day],
              "image": None, "categories": [], "tags": []}  # fmt: skip
    page = '<input type="hidden" class="showdate" value="Saturday 14-Nov-2026 22:30" />'
    venue = {"name": "Liquid Club", "address": "Liquid Club,Triq Hal-Gharghur, L-Iklin", "coordinates": "(35.9121438,14.4625855)"}
    [event] = parse_event(listed, page, "https://www.showshappening.com", "Asia/Jerusalem", venue=venue)
    assert (event.venue_name, event.address) == ("Liquid Club", "Liquid Club,Triq Hal-Gharghur, L-Iklin")
    assert (event.latitude, event.longitude) == (35.9121438, 14.4625855)
    # The page's own fields win over the API's.
    assert parse_event(listed, PAGE, "https://www.showshappening.com", "Asia/Jerusalem", venue=venue)[0].venue_name == (
        "Follow the Sun Boutique Hostel"
    )


TYPES = json.loads((Path(__file__).parent / "fixtures" / "showshappening" / "ticket_types.json").read_text(encoding="utf-8"))


def test_show_id_from_old_and_new_pages() -> None:
    old = '<select class="hidden" id="SelectEventShowsToBuyTicket" name="x"><option selected="selected" value="3226ba42-d920-464c-b88f-08de8e320386">Sat</option>'
    new = '<input type="hidden" class="showid" value="7fd8c510-c22c-4b28-b82e-91ae34223148" />'
    assert show_id(old) == "3226ba42-d920-464c-b88f-08de8e320386"
    assert show_id(new) == "7fd8c510-c22c-4b28-b82e-91ae34223148"
    assert show_id("<html></html>") is None


def test_prices_from_ticket_types() -> None:
    """Bruckner's 7th (MPO, Nov 2026): two types, face value plus booking fee."""
    day = datetime.date.today() + datetime.timedelta(days=7)
    listed = {"title": "Bruckner's 7th", "path": "/mpo/bruckner", "country": "MT", "dates": [day],
              "image": None, "categories": [], "tags": []}  # fmt: skip
    event = parse_event(listed, PAGE, "https://www.showshappening.com", "Asia/Jerusalem", TYPES)[0]
    assert [(t.name, t.price, t.service_fee, t.currency) for t in event.tickets] == [
        ("Main Concourse", Decimal("30.00"), Decimal(3), "EUR"),
        ("Main Concourse Senior Citizens and Students", Decimal("18.00"), Decimal("1.8"), "EUR"),
    ]
    assert event.currency == "EUR" and not event.is_sold_out


def test_free_and_sold_out_types() -> None:
    tiers = ticket_tiers([
        {"Name": "Infant", "Price": "€0.00", "BookingFee": 0, "DisabledType": 0},
        {"Name": "Adult", "Price": "£37.00", "BookingFee": 3.7, "DisabledType": 2},
        {"Name": "Hidden", "Price": "", "DisabledType": 0},
    ])  # fmt: skip
    assert [(t.name, t.price, t.kind, t.status, t.currency) for t in tiers] == [
        ("Infant", Decimal("0.00"), TicketKind.FREE, TierStatus.ON_SALE, "EUR"),
        ("Adult", Decimal("37.00"), TicketKind.PAID, TierStatus.SOLD_OUT, "GBP"),
    ]


def test_sold_out_banner() -> None:
    day = datetime.date.today() + datetime.timedelta(days=7)
    listed = {"title": "Bach", "path": "/f/bach", "country": "MT", "dates": [day], "image": None, "categories": [], "tags": []}
    page = PAGE + "<p> All tickets are sold out! </p>"
    assert parse_event(listed, page, "https://www.showshappening.com", "Asia/Jerusalem")[0].is_sold_out
