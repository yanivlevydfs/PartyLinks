"""Skiddle: a city clubs page's __NEXT_DATA__ rows to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.skiddle import SkiddleScraper, SkiddleSettings, city_rows, parse_event

TZ = "Asia/Jerusalem"
LONDON = ZoneInfo("Europe/London")
ROW = {
    "id": "42256518",
    "EventCode": "CLUB",
    "eventname": "Groove Odyssey 17TH Birthday",
    "cancelled": "0",
    "venue": {"id": 343, "name": "Fabric London", "address": "77A Charterhouse St, London", "town": "London", "postcode_lookup": "London", "postcode": "EC1M 6HJ", "country": "GB", "latitude": 51.519566, "longitude": -0.102463, "type": "Nightclub"},
    "imageurl": "https://d31fr2pwly4c4s.cloudfront.net/e/6/5/2226833_e6a33e56_groove-odyssey-17th-birthday_th.jpg",
    "xlargeimageurl": "https://d31fr2pwly4c4s.cloudfront.net/e/6/5/2226833_e6a33e56_groove-odyssey-17th-birthday_1024.jpg",
    "link": "https://www.skiddle.com/whats-on/London/Fabric-London/Groove-Odyssey-17TH-Birthday/42256518/",
    "startdate": "2027-06-12T14:00:00+00:00",
    "enddate": "2027-06-12T22:00:00+00:00",
    "description": "Join us in celebrating our 17th birthday with a deep house and soulful lineup at Fabric London!",
    "minage": "18",
    "ticketpricing": {"minPrice": 12.5, "maxPrice": 20},
    "ticketUrl": "",
    "currency": "GBP",
    "eventCurrency": "GBP",
    "ticketStatusText": "Buy Tickets",
    "artists": [{"artistid": "123570548", "name": "Natasha Diggs", "image": "https://i.scdn.co/image/ab67", "spotifyartisturl": "spotify:artist:6H2JQ8igAAa5UBr2u496mb"}],
}  # fmt: skip


def _page(rows: list[dict]) -> str:
    data = {"props": {"pageProps": {"initialResults": {"events": rows, "totalCount": str(len(rows))}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_city_rows() -> None:
    assert [r["id"] for r in city_rows(_page([ROW, {"eventname": "no id"}]))] == ["42256518"]
    with pytest.raises(ParseError):
        city_rows('<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {}}}</script>')


def test_row_in_the_venue_clock_with_coordinates_price_and_currency() -> None:
    event = parse_event(ROW, SkiddleSettings(cron="0 0 * * *"), TZ)
    assert event is not None
    # +00:00 on the row is the local clock: 14:00 in London in June is 13:00 UTC.
    assert event.start_at == datetime(2027, 6, 12, 14, tzinfo=LONDON)
    assert event.end_at == datetime(2027, 6, 12, 22, tzinfo=LONDON)
    assert (event.country, event.time_zone, event.city) == ("GB", "Europe/London", "London")
    assert (event.venue_name, event.address) == ("Fabric London", "77A Charterhouse St, London, EC1M 6HJ")
    assert (event.latitude, event.longitude) == (51.519566, -0.102463)
    assert (event.min_price, event.max_price, event.currency) == (Decimal("12.5"), Decimal(20), "GBP")
    assert event.images == [ROW["xlargeimageurl"]]
    assert event.min_age == 18 and not event.is_sold_out and event.extra == {}
    assert event.artists == ["Natasha Diggs"]
    assert event.artist_profiles[0].spotify_url == "https://open.spotify.com/artist/6H2JQ8igAAa5UBr2u496mb"
    settings = get_settings()
    assert Enricher(settings.enrichment, settings.timezone).enrich(event).genres


def test_cancelled_sold_out_and_seller_link() -> None:
    assert parse_event(ROW | {"cancelled": "1"}, SkiddleSettings(cron="0 0 * * *"), TZ) is None
    other = ROW | {
        "ticketStatusText": "Sold Out",
        "ticketUrl": "https://seller.example/t/1",
        "enddate": "2027-06-12T14:00:00+00:00",
    }
    event = parse_event(other, SkiddleSettings(cron="0 0 * * *"), TZ)
    assert event is not None and event.is_sold_out and event.end_at is None
    assert event.extra == {"purchase_url": "https://seller.example/t/1"}
    street_town = ROW | {"venue": ROW["venue"] | {"town": "1 Sports City Way"}}
    assert parse_event(street_town, SkiddleSettings(cron="0 0 * * *"), TZ, "Manchester").city == "Manchester"


def test_waf_challenge_is_blocked() -> None:
    scraper = SkiddleScraper.__new__(SkiddleScraper)
    challenge = httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
    assert scraper.is_blocked(challenge)
    assert not scraper.is_blocked(httpx.Response(200, text=_page([ROW])))
