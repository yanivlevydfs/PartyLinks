"""DICE: sitemaps, the run plan, event and venue data routes to ScrapedEvent / profiles."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.dice import (
    DiceSettings,
    Listed,
    build_id,
    event_row,
    known_slugs,
    parse_event,
    plan_run,
    sitemap_events,
    slug_date,
    stable_slot,
    venue_profile,
)
from partylinks.venue_profiles import VENUE_PROFILE

FIXTURES = Path(__file__).parent / "fixtures" / "dice"
TZ = "Asia/Jerusalem"
S = DiceSettings(cron="14 5,17 * * *")


def _row(name: str) -> dict[str, Any]:
    row = event_row(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
    assert row is not None
    return row


def _genres(event: Any) -> list[str]:
    settings = get_settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None
    return enriched.genres


def test_sitemap_lists_event_pages_with_lastmod_and_slug_date_but_no_bundle() -> None:
    xml = (FIXTURES / "sitemap_events.xml").read_text(encoding="utf-8")
    items = sitemap_events(xml, "https://dice.fm", date(2026, 11, 19), 60)
    assert [i.slug for i in items][:2] == [
        "kxpvvp-hit-anatomy-the-cube-guys-21st-nov-cinema-teatro-wagner-milano-tickets",
        "g7o225-lexmas-thursdays-with-djs-till-3am-17th-dec-the-lexington-london-tickets",
    ]
    assert len(items) == 4  # the bundle page is not an event
    assert items[0].lastmod == datetime(2026, 11, 13, 15, 4, 8, 967688, tzinfo=UTC)
    assert items[0].day == date(2026, 11, 21)
    assert items[3].day == date(2027, 5, 14)  # "14th-may" is next year's
    # Seen on 25 Nov, the 21st's show is over: not read at all.
    assert len(sitemap_events(xml, "https://dice.fm", date(2026, 11, 25), 60)) == 2


def test_slug_dates() -> None:
    today = date(2026, 11, 19)
    assert slug_date("abc-the-show-18th-dec-venue-london-tickets", today, 60) == date(2026, 12, 18)
    assert slug_date("abc-show-1st-oct-venue-tickets", today, 60) == date(2026, 10, 1)  # recently past
    assert slug_date("abc-show-3rd-feb-venue-tickets", today, 60) == date(2027, 2, 3)
    assert slug_date("abc-show-29th-feb-venue-tickets", today, 60) is None  # neither 2026 nor 2027 has one
    assert slug_date("abc-nye-party-tickets", today, 60) is None


def test_build_id_and_known_slugs() -> None:
    assert (
        build_id('<script id="__NEXT_DATA__">{"page":"/","buildId":"zPCE2fSsxAOqGa4i2mwX1"}</script>')
        == "zPCE2fSsxAOqGa4i2mwX1"
    )
    with pytest.raises(ParseError):
        build_id("<html></html>")
    urls = [
        "https://dice.fm/event/k63wr9-a-tickets",
        "https://dice.fm/event/6af6e5fac4992d0001b4d821",
        "https://x.com/event/z",
    ]
    assert known_slugs(urls, "https://dice.fm") == {"k63wr9-a-tickets", "6af6e5fac4992d0001b4d821"}


def test_plan_reads_new_changed_soon_then_the_slot_nearest_first_within_the_budget() -> None:
    now = datetime(2026, 11, 19, 3, tzinfo=UTC)
    old = now - timedelta(days=10)

    def item(slug: str, day: date | None, lastmod: datetime = old) -> Listed:
        return Listed(slug, lastmod, day)

    slot = 3
    in_slot = next(f"far-{i}" for i in range(100) if stable_slot(f"far-{i}", S.cycle_runs) == slot)
    off_slot = next(f"far-{i}" for i in range(100) if stable_slot(f"far-{i}", S.cycle_runs) != slot)
    listed = [
        item("new-late", date(2027, 3, 1)),
        item("new-soon", date(2026, 11, 20)),
        item("changed", date(2027, 1, 5), now - timedelta(hours=5)),
        item("tomorrow", date(2026, 11, 20)),
        item(in_slot, date(2027, 2, 1)),
        item(off_slot, date(2027, 2, 1)),
        item("undated-new", None),
    ]
    known = {"changed", "tomorrow", in_slot, off_slot}
    plan, why = plan_run(listed, known, now, slot, S)
    assert [i.slug for i in plan] == ["new-soon", "tomorrow", "changed", in_slot, "new-late", "undated-new"]
    assert why == "new 3/3, changed 1/1, soon 1/1, slot 1/1"
    # A small budget keeps the new ones first, nearest first.
    small, why = plan_run(listed, known, now, slot, S.model_copy(update={"max_pages_per_run": 2}))
    assert [i.slug for i in small] == ["new-soon", "new-late"]
    assert why.startswith("new 2/3, changed 0/1")


def test_party_in_the_venue_zone_pinned_priced_and_tied_to_its_venue_profile() -> None:
    event = parse_event(_row("event_party.json"), S, TZ)
    assert event is not None
    london = ZoneInfo("Europe/London")
    assert event.title == "The George Michael Disco!"
    assert (
        str(event.url)
        == "https://dice.fm/event/k63wr9-the-george-michael-disco-18th-dec-the-clapham-grand-london-tickets"
    )
    assert event.extra["purchase_url"] == str(event.url)
    assert event.source_event_id == "6adb8c4379b93500018d5afe"
    assert event.start_at == datetime(2026, 12, 18, 22, tzinfo=london)
    assert event.end_at == datetime(2026, 12, 19, 2, tzinfo=london)
    assert (event.country, event.time_zone, event.city) == ("GB", "Europe/London", "London")
    assert event.venue_name == "The Clapham Grand"
    assert event.address == "The Clapham Grand, 21-25 St John's Hill, London SW11 1TT, UK"
    assert (event.latitude, event.longitude) == pytest.approx((51.4629923, -0.1693688))
    assert (event.min_price, event.currency, event.is_sold_out) == (Decimal(15), "GBP", False)
    assert [(t.price, t.currency) for t in event.tickets] == [(Decimal(15), "GBP")]
    assert event.min_age == 18
    assert event.organizer == "The Clapham Grand"
    assert event.extra[VENUE_PROFILE] == "dice-1061"
    assert (event.source_genres, event.event_types) == (["party"], ["Party"])
    assert event.images and event.images[0].startswith("https://dice-media.imgix.net/")
    assert event.description and "George Michael" in event.description
    assert "nightclubs" in _genres(event)


def test_gig_with_artists_feeds_the_lineup_and_artist_profiles() -> None:
    event = parse_event(_row("event_gig_artists.json"), S, TZ)
    assert event is not None
    assert event.start_at == datetime(2027, 1, 1, 22, tzinfo=ZoneInfo("America/New_York"))
    assert event.start_at is not None and event.start_at.utcoffset() == timedelta(hours=-5)
    assert (event.city, event.country, event.time_zone) == ("Miami", "US", "America/New_York")
    assert event.artists == ["horsegiirL", "ALI RQ"]
    assert [(p.name, p.source) for p in event.artist_profiles] == [("horsegiirL", "dice"), ("ALI RQ", "dice")]
    assert event.artist_profiles[0].image_url and event.artist_profiles[1].image_url is None
    assert event.source_genres == ["concert"]
    assert event.min_price is None and event.tickets == [] and event.currency == "USD"  # announced, unpriced
    assert "live_show" in _genres(event)


def test_festival_on_several_venues_is_placed_by_address_not_the_city_centre() -> None:
    event = parse_event(_row("event_festival.json"), S, TZ)
    assert event is not None
    assert event.latitude is None and event.longitude is None  # the pin is Bristol's centre
    assert event.address and event.city == "Bristol"
    assert event.source_genres == ["concert", "festival"]
    assert event.min_price == Decimal("25.5") and event.min_age == 14
    assert {"festival", "live_show"} <= set(_genres(event))


def test_comedy() -> None:
    event = parse_event(_row("event_comedy.json"), S, TZ)
    assert event is not None
    assert event.source_genres == ["comedy"] and event.event_types == ["Comedy"]
    assert "stand_up" in _genres(event)


# Every DICE type (the browse filters of Nov 2026) and the category it lands in.
TYPE_CASES = [
    ("music:gig", "Gigs", "live_show"),
    ("music:dj", "DJ", "electronic"),
    ("music:party", "Party", "nightclubs"),
    ("music:playback", "Playback", "live_show"),
    ("music:artistsigning", "Artist signing", "meetups"),
    ("culture:comedy", "Comedy", "stand_up"),
    ("culture:social", "Social", "meetups"),
    ("culture:workshop", "Workshop", "workshops"),
    ("culture:art", "Art", "exhibitions"),
    ("culture:theatre", "Theatre", "theatre"),
    ("culture:talks", "Talk", "lectures"),
    ("culture:podcast", "Podcast", "lectures"),
    ("culture:sport", "Sport", "sport"),
    ("culture:film", "Film", "cinema"),
    ("culture:foodanddrink", "Food & drink", "food"),
    ("culture:wellbeing", "Wellbeing", "yoga_wellness"),
]


def test_every_dice_type_is_mapped() -> None:
    assert {v for v, _, _ in TYPE_CASES} == set(S.type_genres)


@pytest.mark.parametrize(("value", "title", "genre"), TYPE_CASES)
def test_each_type_lands_in_its_category(value: str, title: str, genre: str) -> None:
    row = _row("event_party.json") | {
        "name": "Untitled night",
        "about": {"description": None},
        "tags_types": [{"value": value, "title": title}],
    }
    event = parse_event(row, S, TZ)
    assert event is not None and event.event_types == [title]
    assert genre in _genres(event)


def test_hidden_cancelled_online_and_unnamed_events_are_left_out() -> None:
    row = _row("event_party.json")
    assert parse_event(row | {"is_hidden_event": True}, S, TZ) is None
    assert parse_event(row | {"status": "cancelled"}, S, TZ) is None
    assert parse_event(row | {"venues": []}, S, TZ) is None
    assert parse_event(row | {"name": " "}, S, TZ) is None
    sold = parse_event(row | {"status": "sold-out"}, S, TZ)
    assert sold is not None and sold.is_sold_out and sold.tickets[0].status.value == "sold_out"


def test_event_row_of_a_removed_page() -> None:
    assert event_row({"pageProps": {"statusCode": 404, "initialState": json.dumps({"event": {}})}}) is None
    assert event_row({"pageProps": {}}) is None
    with pytest.raises(ParseError):
        event_row({"pageProps": {"initialState": "{not json"}})


def test_venue_page_profile() -> None:
    profile = venue_profile(json.loads((FIXTURES / "venue.json").read_text(encoding="utf-8")), "https://dice.fm")
    assert profile is not None
    assert (profile.key, profile.name, profile.source) == ("dice-1061", "The Clapham Grand", "dice")
    assert profile.profile_url == "https://dice.fm/venue/the-clapham-grand-ry8v"
    assert profile.about and profile.about.startswith("First opened in 1900")
    assert (
        "https://www.instagram.com/thegrandclapham/" in profile.links and "https://claphamgrand.com/" in profile.links
    )
    assert (profile.latitude, profile.longitude) == pytest.approx((51.4629923, -0.1693688))
    assert profile.logo_url and profile.logo_url.startswith("https://dice-media.imgix.net/")
    assert venue_profile({"pageProps": {"profile": {"venue": {"name": "x"}}}}, "https://dice.fm") is None


def test_a_city_without_a_country_code_keeps_the_venue_zone() -> None:
    row = _row("event_party.json")
    venue = row["venues"][0] | {"city": {k: v for k, v in row["venues"][0]["city"].items() if k != "country_code"}}
    event = parse_event(row | {"venues": [venue]}, S, TZ)
    assert event is not None and event.country is None and event.time_zone == "Europe/London"
