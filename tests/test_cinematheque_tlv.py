"""Tel Aviv Cinematheque: PRES screenings to one event per feature and business day."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.cinematheque_tlv import TelAvivCinemathequeSettings, build_event, group_screenings
from partylinks.scrapers.pres import parse_ticket_groups

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/cinematheque_tlv"
LISTING = json.loads((FIXTURES / "presentations.json").read_text("utf-8"))
FEATURES = {str(f["id"]): f for f in json.loads((FIXTURES / "features.json").read_text("utf-8"))}
TIERS = parse_ticket_groups(json.loads((FIXTURES / "ticket_groups.json").read_text("utf-8")))
SETTINGS = TelAvivCinemathequeSettings(
    cron="5 10,22 * * *",
    api_url="https://cintlv.presglobal.store",
    presentations_path="/api/presentations/",
    features_path="/api/features/",
    ticket_groups_path="/api/presentations/{id}/ticketGroups",
    order_url="https://cintlv.presglobal.store/order/{id}",
)


def events() -> dict[str, ScrapedEvent]:
    groups = group_screenings(LISTING, TZ, date(2026, 10, 22), 7)
    built = [
        build_event(movie, day, shows, FEATURES.get(movie, {}), TIERS, SETTINGS)
        for (movie, day), shows in groups.items()
    ]
    return {e.source_event_id: e for e in built}


def test_screenings_grouped_per_movie_and_day_within_window() -> None:
    by_id = events()
    # The 31.10 screening is past the 7-day window.
    assert set(by_id) == {
        "21546:tlv:2026-10-22",
        "21546:tlv:2026-10-23",
        "21545:tlv:2026-10-24",
        "21003:tlv:2026-10-25",
    }
    film = by_id["21546:tlv:2026-10-22"]
    assert film.title == "מתוך אהבה" and film.start_at is not None
    assert film.start_at.isoformat() == "2026-10-22T18:15:00+03:00"
    assert film.extra["showtimes"] == ["2026-10-22T18:15:00+03:00", "2026-10-22T20:45:00+03:00"]
    assert film.description is not None and film.description.endswith("הקרנות: 18:15, 20:45")
    assert (film.venue_name, film.address, film.city) == ("סינמטק תל אביב", "הארבעה 5", "תל אביב")
    assert film.extra["halls"] == ["אולם 5"] and film.extra["language"] == "fr"
    assert str(film.url) == "https://cintlv.presglobal.store/order/135019"
    assert film.min_price == Decimal(45) and film.tickets[0].name == "רגיל"


def test_film_with_talk_stays_cinema_but_course_meeting_is_a_lecture() -> None:
    by_id = events()
    talk = by_id["21545:tlv:2026-10-24"]
    assert talk.source_genres == ["קולנוע"]
    assert talk.image_url == "https://cdn.presglobal.pro/cintlv-prod/feature/c79f638b-6272-4c8c-a357-7b5b44f91611"
    meeting = by_id["21003:tlv:2026-10-25"]
    assert meeting.source_genres == ["הרצאה"]
    assert meeting.extra["series"] == ["תמול שלשום 2026 חלק ב'"]
