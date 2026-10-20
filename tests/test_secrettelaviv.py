"""Secret Tel Aviv parser tests against trimmed real pages (one listing page, two event pages)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import secrettelaviv

FIXTURES = Path(__file__).parent / "fixtures" / "secrettelaviv"
TZ = "Asia/Jerusalem"
SITE = "https://www.secrettelaviv.com"
CFG = secrettelaviv.SecretTelAvivSettings(cron="52 */3 * * *", site_url=SITE, listing_paths=["/tickets"])


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_listing_rows_give_event_urls_only() -> None:
    urls = secrettelaviv.parse_listing(_page("listing.html"), CFG)
    assert urls == [
        f"{SITE}/tickets/deep-roots-486-mitzpor",
        f"{SITE}/tickets/chorole-ala-rampa",
        f"{SITE}/tickets/happy-happy-joy-joy-callas-tlv",
        f"{SITE}/tickets/karaoke-night-kuli-alma-5",
    ]  # category / pagination links outside the rows are ignored


def test_event_page_fields() -> None:
    url = f"{SITE}/tickets/alien-transport-frequency-gagarin-club"
    event = secrettelaviv.parse_event(_page("event_gagarin.html"), url, CFG, TZ)
    assert event.source_event_id == "alien-transport-frequency-gagarin-club"
    assert event.title == "Alien Transport & Frequency @ Gagarin Club"
    assert event.start_at == datetime(2026, 10, 26, 23, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at is None  # "11:55 pm" is the site's placeholder end
    assert event.venue_name == "Gagarin Club" and event.address is None
    assert event.source_genres == [
        "international artists",
        "israeli musicians",
        "parties",
        "rave",
    ]
    assert event.image_url == f"{SITE}/wp-content/uploads/2026/10/image-248.jpg"
    assert event.extra["purchase_url"] == "https://www.instagram.com/p/DeZn7lAKMF0/"
    assert event.description and event.description.startswith("English")
    assert "Crazy Astronaut" in event.description
    assert "Location" not in event.description and "Secret Deals" not in event.description


def test_multi_day_event_runs_to_last_day() -> None:
    url = f"{SITE}/tickets/soundtrack-tel-aviv-film-festival-tel-aviv-cinematheque"
    event = secrettelaviv.parse_event(_page("event_soundtrack.html"), url, CFG, TZ)
    assert event.start_at == datetime(2026, 11, 24, 18, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2026, 11, 28, 23, 0, tzinfo=ZoneInfo(TZ))
    assert event.venue_name == "Tel Aviv Cinematheque"


def test_end_after_midnight_rolls_to_next_day() -> None:
    start, end = secrettelaviv.parse_when("Fri 30 October 2026 | 11:00 pm - 5:00 am", "11:55 pm", ZoneInfo(TZ))
    assert start == datetime(2026, 10, 30, 23, 0, tzinfo=ZoneInfo(TZ))
    assert end == datetime(2026, 10, 31, 5, 0, tzinfo=ZoneInfo(TZ))
