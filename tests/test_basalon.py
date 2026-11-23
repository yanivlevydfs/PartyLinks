"""Basalon parser tests on a real category page's cards, a listing page of the site's pagination call and event
pages' JSON-LD and booking calendar (November 2026)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import basalon

FIXTURES = Path(__file__).parent / "fixtures" / "basalon"
TZ = "Asia/Jerusalem"
CFG = basalon.BasalonSettings.model_validate(Settings().sources["basalon"])
TODAY = date(2026, 11, 22)


def page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def page_json(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def card(post_id: str, labels: list[str]) -> basalon.Card:
    return basalon.Card(post_id, f"https://basalon.co.il/event/{post_id}/", "x", TODAY, labels)


def test_cards_dates_and_appointment_only() -> None:
    cards = {c.post_id: c for c in basalon.parse_cards(page("lectures"), CFG, TODAY)}
    assert len(cards) == 6
    talk = cards["1487277"]
    assert talk.title.startswith("אוזן קשבת – 100 שנים למיילס דייויס")
    assert talk.url.startswith("https://basalon.co.il/event/")
    assert talk.when == date(2026, 11, 27)  # "יום ו׳, 27.11 ב- 12:45"
    assert cards["1336386"].when == date(2026, 11, 23)  # "מחר ב- 19:00"
    assert cards["1471866"].when == TODAY  # "היום ב- 20:00"
    assert cards["1459647"].when == date(2027, 1, 15)  # "יום ו׳, 15.1.27 ב- 13:00"
    # Booked by appointment / at the customer's home: no date on the card, not read further.
    assert cards["1342027"].when is None and cards["1485243"].when is None


def test_card_date_rolls_into_next_year() -> None:
    assert basalon.card_date("יום ב׳, 4.1 ב- 20:00", CFG, TODAY) == date(2027, 1, 4)
    assert basalon.card_date("בתיאום מראש", CFG, TODAY) is None
    assert basalon.card_date(None, CFG, TODAY) is None


def test_show_at_a_pub() -> None:
    show = basalon.parse_event(page("1487234"), card("1487234", ["הופעות"]), CFG, TZ)
    assert show is not None
    assert show.source_event_id == "1487234:2026-11-24"
    assert show.title == "אפשר להגזים? | רוק עברי מקורי"
    assert show.start_at == datetime(2026, 11, 24, 21, 0, tzinfo=ZoneInfo(TZ))
    assert show.end_at == datetime(2026, 11, 24, 23, 0, tzinfo=ZoneInfo(TZ))
    assert (show.venue_name, show.address, show.city) == ("דרך יפו 35, חיפה", "דרך יפו 35, חיפה", "חיפה")
    assert show.organizer == "Elis Pub"
    assert [(t.price, t.is_free) for t in show.tickets] == [(0, True)] and show.min_price == 0
    assert show.images and show.images[0].startswith("https://basalon.co.il/wp-content/uploads/")
    assert show.source_genres == ["הופעות"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and enriched.genres


def test_paid_show_tiers_and_genres() -> None:
    show = basalon.parse_event(page("1426935"), card("1426935", ["הופעות", "הרצאות"]), CFG, TZ)
    assert show is not None and show.min_price == 60 and show.max_price == 60
    assert show.address == "הנגר 8, הוד השרון" and show.city == "הוד השרון"
    assert show.title == "סטרלינג אוסטין – הבריטים באים!" and show.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and enriched.genres


def test_the_town_is_the_place_names_last_part() -> None:
    """The page's addressLocality is the name's second part cut at the '"': "שדרות הפלי" (an avenue in Haifa)
    filed Ensemble Meitar's church concerts under the town Sderot (5 events, Nov 2026)."""
    import json

    node = {
        "@type": "Event",
        "name": "אנסמבל מצו מארח בחיפה",
        "startDate": "2026-12-05T20:00:00+02:00",
        "location": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "כנסיית בית החסד",
                "addressLocality": "שדרות הפלי",
            },
            "name": 'כנסיית בית החסד, שדרות הפלי"ם, חיפה, ישראל',
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'
    show = basalon.parse_event(html, card("1", ["הופעות"]), CFG, TZ)
    assert show is not None and show.city == "חיפה"
    assert show.address == 'כנסיית בית החסד, שדרות הפלי"ם, חיפה'
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and enriched.city == "חיפה"


def test_private_workshop_and_hidden_address_are_dropped() -> None:
    assert basalon.parse_event(page("1052068"), card("1052068", ["סדנאות"]), CFG, TZ) is None  # "סדנה פרטית לזוג"
    assert basalon.parse_event(page("1273829"), card("1273829", ["הרצאות"]), CFG, TZ) is None  # a locality only
    assert basalon.parse_event("<html></html>", card("984978", ["סדנאות"]), CFG, TZ) is None  # a gift card


def test_listing_page_of_the_pagination_call() -> None:
    """``admin-ajax.php?action=el_search_map`` answers {"result": cards, "pagination": "148 Results Found"}."""
    payload = json.loads(page_json("listing"))
    cards = basalon.parse_cards(payload["result"], CFG, TODAY)
    assert [c.post_id for c in cards] == ["1430031", "1015656", "1187099"]
    assert cards[0].when == date(2026, 11, 26) and cards[0].url.startswith("https://basalon.co.il/event/")
    assert cards[2].when is None  # booked by appointment
    assert "148 Results Found" in payload["pagination"]


def test_every_listing_page_is_read_to_the_last() -> None:
    """The category pages stop at page 10 (kids held 1,075 cards, Nov 2026): the call is read until a page
    holds no card, every category, with no page cap."""
    payload = json.loads(page_json("listing"))
    asked: list[tuple[str, str]] = []

    async def fake_post_json(url: str, *, data: dict[str, str], **_: object) -> Any:
        asked.append((data["data[cat]"], data["data[paged]"]))
        assert url == "https://basalon.co.il/wp-admin/admin-ajax.php" and data["action"] == "el_search_map"
        return payload if int(data["data[paged]"]) <= 12 else {"result": "", "pagination": "0 Results Found"}

    scraper = basalon.BasalonScraper(CFG, HttpSettings(), TZ)
    scraper.post_json = fake_post_json  # type: ignore[method-assign]
    category = basalon.BasalonCategory(slug="kids", label="ילדים")
    cards = asyncio.run(scraper._list_category(category, TODAY))
    assert len(cards) == 3
    # The same cards again on page 2 are no new card: the loop ends there (a real page 2 has others).
    assert asked == [("kids", "1"), ("kids", "2")]
    assert {c.slug for c in CFG.categories} >= {"music", "kids", "workshop", "food", "body-mind", "tour", "group"}


def test_every_date_of_the_calendar_is_an_event() -> None:
    html = page("1016721")
    slots = basalon.calendar_slots(html, TZ)
    assert len(slots) == 20 and slots[0].start == datetime(2026, 11, 27, 10, 0, tzinfo=ZoneInfo(TZ))
    assert slots[0].end == datetime(2026, 11, 27, 12, 30, tzinfo=ZoneInfo(TZ))
    now = datetime(2026, 11, 23, 12, 0, tzinfo=ZoneInfo(TZ))
    events = basalon.parse_events(html, card("1016721", ["סיורים"]), CFG, TZ, now)
    assert len(events) == 20
    assert events[0].source_event_id == "1016721:2026-11-27"
    assert events[1].source_event_id == "1016721:2026-12-04"
    assert events[1].start_at == datetime(2026, 12, 4, 10, 0, tzinfo=ZoneInfo(TZ))
    assert len({e.source_event_id for e in events}) == 20
    assert all(e.city and e.address and e.title == events[0].title for e in events)
    # Past dates are left out.
    later = basalon.parse_events(html, card("1016721", ["סיורים"]), CFG, TZ, now + timedelta(days=30))
    first = later[0].start_at
    assert len(later) < 20 and first is not None and first > now + timedelta(days=30)


def _calendar(base: str, starts: list[datetime], sold: int | None = None) -> str:
    rows = [
        {
            "title": f'<a class="event_schedule {"sold" if i == sold else "avail"}" href="javascript://">x</a>',
            "start": f"{start:%Y-%m-%dT%H:%M}",
            "end": f"{start + timedelta(hours=2):%Y-%m-%dT%H:%M}",
            "url": "#",
        }
        for i, start in enumerate(starts)
    ]
    cal = json.dumps(rows).replace("&", "&amp;").replace('"', "&quot;")
    return base.split('<div class="ticket-calendar"')[0] + f'<div class="fullcalendar" data-listevent="{cal}"></div>'


def test_two_slots_a_day_and_a_sold_out_date() -> None:
    day = datetime(2026, 12, 1, 10, 0)
    starts = [day, day + timedelta(hours=4), day + timedelta(days=7)]
    html = _calendar(page("1016721"), starts, sold=2)
    now = datetime(2026, 11, 23, 12, 0, tzinfo=ZoneInfo(TZ))
    events = basalon.parse_events(html, card("1016721", ["סיורים"]), CFG, TZ, now)
    assert [e.source_event_id for e in events] == [
        "1016721:2026-12-01",
        "1016721:2026-12-01:1400",
        "1016721:2026-12-08",
    ]
    assert [e.is_sold_out for e in events] == [False, False, True]


def test_a_booking_calendar_of_time_slots_stays_one_event() -> None:
    """More than two slots a week in its first 4 weeks (a pottery yard twice a day, a couple's workshop every
    morning but Saturday): time slots, not dated sessions; the JSON-LD's next date only."""
    now = datetime(2026, 11, 23, 12, 0, tzinfo=ZoneInfo(TZ))
    day = datetime(2026, 12, 1, 9, 0)
    twice_a_day = [day + timedelta(days=d, hours=h) for d in range(60) for h in (0, 3)]
    mornings = [day + timedelta(days=d) for d in range(120) if (day + timedelta(days=d)).weekday() != 5]
    for starts in (twice_a_day, mornings):
        events = basalon.parse_events(_calendar(page("1016721"), starts), card("1016721", ["סדנאות"]), CFG, TZ, now)
        assert [e.source_event_id for e in events] == ["1016721:2026-11-27"]


def test_twice_a_week_sessions_are_each_an_event() -> None:
    """Two sessions a week for 6 months (a knitting workshop on Mondays and Thursdays): 8 in 4 weeks, each one
    a dated event."""
    now = datetime(2026, 11, 23, 12, 0, tzinfo=ZoneInfo(TZ))
    day = datetime(2026, 11, 30, 18, 0)  # a Monday
    starts = [day + timedelta(weeks=w, days=d) for w in range(26) for d in (0, 3)]
    events = basalon.parse_events(_calendar(page("1016721"), starts), card("1016721", ["סדנאות"]), CFG, TZ, now)
    assert len(events) == 52
    assert events[1].source_event_id == "1016721:2026-12-03"
