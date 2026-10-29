"""Yotzim: category pages and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.yotzim import category_genres, parse_event

TZ = "Asia/Jerusalem"
SITE = "https://yotzim.com"
FIXTURES = Path(__file__).parent / "fixtures/yotzim"


def _event(name: str, slug: str, genres: list[str] | None = None):
    return parse_event((FIXTURES / name).read_text("utf-8"), f"{SITE}/event/{slug}", TZ, genres or [])


def test_categories_tag_the_events_they_list() -> None:
    pages = {p: (FIXTURES / f).read_text("utf-8") for p, f in [("/events", "events.html"), ("/events/nature", "nature.html")]}
    genres = category_genres(pages, {"/events": [], "/events/nature": ["nature party"]}, SITE)
    assert len(genres) >= 20 and all(url.startswith(f"{SITE}/event/") for url in genres)
    tagged = [url for url, words in genres.items() if words == ["nature party"]]
    assert tagged and f"{SITE}/event/vortex-festival-12-13-nov-2026" in tagged
    # The "recently" rail (events already over) is never fetched.
    assert f"{SITE}/event/back-to-the-mountains" not in genres
    # A holiday already over: "what was at Rosh Hashana" is skipped, "meanwhile" upcoming is not tagged.
    rosh = {"/holidays/rosh-hashana": (FIXTURES / "rosh-hashana.html").read_text("utf-8")}
    upcoming = category_genres(rosh, {"/holidays/rosh-hashana": ["holidays"]}, SITE)
    assert upcoming and not any(upcoming.values())


def test_suggested_events_are_not_tagged() -> None:
    # No hip hop night or wine evening coming: both pages show "other events that might fit".
    for path, name in [("/genres/hip-hop", "hip-hop.html"), ("/events/wine", "wine.html")]:
        genres = category_genres({path: (FIXTURES / name).read_text("utf-8")}, {path: ["x"]}, SITE)
        assert genres and not any(words for url, words in genres.items() if "halloween" in url)


def test_city_only_night_has_no_venue() -> None:
    e = _event("radio-berlin-face-off.html", "radio-berlin-face-off", ["techno", "hard techno"])
    assert e is not None and e.source_event_id == "radio-berlin-face-off"
    assert e.start_at.isoformat() == "2026-10-29T23:59:00+02:00" and e.time_known
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-30T08:00:00+02:00"
    assert e.venue_name is None and e.city == "תל אביב" and e.country is None
    assert (e.min_price, e.currency) == (Decimal(144), "ILS") and e.source_genres == ["techno", "hard techno"]
    assert e.images and e.images[0].startswith("https://") and e.organizer is None


def test_free_night_with_venue_and_site_as_organizer() -> None:
    e = _event("rising-dust-free.html", "rising-dust-x-melodic-friday-open-air")
    assert e is not None and e.venue_name == "מון צ׳יילד" and e.address == "האנגר 23"
    assert e.min_price == 0 and e.tickets[0].price == 0
    assert e.organizer is None  # "יוצאים!" is the guide itself


def test_koh_phangan_in_local_time_with_age() -> None:
    e = _event("utopia-koh-phangan.html", "utopia-events-31-12-2026-new-year-rave-koh-phangan")
    assert e is not None and e.country == "TH" and e.venue_name == "Than Sadet"
    assert e.start_at.isoformat() == "2026-12-31T21:00:00+07:00" and e.min_age == 18
