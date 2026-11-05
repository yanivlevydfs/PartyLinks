import datetime

from partylinks.scrapers.showshappening import parse_event

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
