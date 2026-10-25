"""Yellow Submarine and HaHalutz 33 parsers against their real pages (October 2026)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.scrapers import halutz, yellowsubmarine

FIXTURES = Path(__file__).parent / "fixtures"
TZ = "Asia/Jerusalem"


def test_yellowsubmarine_archive_and_show_page() -> None:
    cfg = yellowsubmarine.YellowSubmarineSettings.model_validate(Settings().sources["yellowsubmarine"])
    cards = yellowsubmarine.parse_listing((FIXTURES / "yellowsubmarine" / "events.html").read_text(encoding="utf-8"))
    assert len(cards) == 36
    events = [yellowsubmarine.parse_card(c, cfg, TZ, today=date(2026, 10, 25)) for c in cards]
    assert all(events)
    piazzolla = next(e for e in events if e and e.url.path.endswith("/piazzolla/"))
    # The show starts at 21:30 Israeli time (doors 20:30); the page's JSON-LD calls that "21:30Z".
    assert piazzolla.start_at == datetime(2026, 10, 28, 21, 30, tzinfo=ZoneInfo(TZ))
    assert (piazzolla.extra or {})["doors_open"] == "20:30"
    assert piazzolla.city == "ירושלים" and piazzolla.latitude
    assert piazzolla.images and "600x338" not in piazzolla.images[0]
    # "6.1" read in October is next January.
    assert max(e.start_at for e in events if e and e.start_at).year == 2027

    yellowsubmarine.add_page(piazzolla, (FIXTURES / "yellowsubmarine" / "event.html").read_text(encoding="utf-8"))
    assert (piazzolla.min_price, piazzolla.max_price) == (Decimal(90), Decimal(115))
    assert len(piazzolla.tickets) == 3
    assert (piazzolla.extra or {})["purchase_url"] == "https://www.eventer.co.il/8qn2f"
    assert "פיאצולה" in (piazzolla.description or "")
    assert piazzolla.start_at == datetime(2026, 10, 28, 21, 30, tzinfo=ZoneInfo(TZ))

    festival = next(e for e in events if e and e.url.path.endswith("/imsf26-3/"))
    yellowsubmarine.add_page(festival, (FIXTURES / "yellowsubmarine" / "event_free.html").read_text(encoding="utf-8"))
    assert festival.min_price == 0 and festival.tickets[0].is_free  # "כניסה חופשית"
    assert festival.start_at == datetime(2026, 11, 17, 23, 10, tzinfo=ZoneInfo(TZ))


def test_halutz_posts_and_pages() -> None:
    cfg = halutz.HalutzSettings.model_validate(Settings().sources["halutz"])
    cards = halutz.parse_listing(json.loads((FIXTURES / "halutz" / "posts.json").read_text(encoding="utf-8")))
    assert len(cards) == 31
    assert any(c["title"] == "ג'אז בחלוץ! TOMER ZUK – YANUKA" for c in cards)  # entities decoded

    noise = halutz.parse_card(
        next(c for c in cards if c["id"] == "8635"),
        (FIXTURES / "halutz" / "post.html").read_text(encoding="utf-8"),
        cfg,
        TZ,
    )
    assert noise is not None and noise.title == "רעש לבן"
    assert noise.start_at == datetime(2027, 1, 4, 21, 30, tzinfo=ZoneInfo(TZ))  # "דלתות 20:30 ★ מופע 21:30"
    assert noise.venue_name == "החלוץ 33" and noise.city == "באר שבע"
    assert (noise.extra or {})["purchase_url"] == "https://www.eventer.co.il/nxd2f"
    assert (noise.extra or {})["doors_open"] == "20:30"
    assert noise.images and noise.images[0].startswith("https://www.halutz.co.il/wp-content/uploads/")
    assert "רעש לבן" in (noise.description or "") and "iframe" not in (noise.description or "")

    # A Sderot (loc p32) show takes that stage's place.
    amir = halutz.parse_card(
        next(c for c in cards if c["id"] == "8582"),
        (FIXTURES / "halutz" / "post_sderot.html").read_text(encoding="utf-8"),
        cfg,
        TZ,
    )
    assert amir is not None and amir.city == "שדרות" and amir.latitude == cfg.branches[174].latitude
    assert amir.start_at == datetime(2026, 10, 29, 21, 0, tzinfo=ZoneInfo(TZ))

    # No date on the page: no event.
    assert halutz.parse_card(cards[0], "<html><body></body></html>", cfg, TZ) is None


def test_halutz_free_entry() -> None:
    cfg = halutz.HalutzSettings.model_validate(Settings().sources["halutz"])
    cards = halutz.parse_listing(json.loads((FIXTURES / "halutz" / "posts.json").read_text(encoding="utf-8")))
    jazz = halutz.parse_card(
        next(c for c in cards if c["id"] == "8570"),
        (FIXTURES / "halutz" / "post.html").read_text(encoding="utf-8"),
        cfg,
        TZ,
    )
    assert jazz is not None and jazz.min_price == 0 and jazz.tickets[0].is_free  # "הכניסה חופשית", no Eventer link
    assert "purchase_url" not in (jazz.extra or {})


def test_yellowsubmarine_festival_stages() -> None:
    cfg = yellowsubmarine.YellowSubmarineSettings.model_validate(Settings().sources["yellowsubmarine"])
    cards = yellowsubmarine.parse_listing((FIXTURES / "yellowsubmarine" / "events.html").read_text(encoding="utf-8"))
    events = {
        c["url"].rstrip("/").rsplit("/", 1)[-1]: yellowsubmarine.parse_card(c, cfg, TZ, date(2026, 10, 25))
        for c in cards
    }
    # One festival title, many stages: the stage joins the title, and a stage away from the club is its place.
    tower = events["imsf26-4"]
    assert tower is not None and tower.title == "פסטיבל חשיפה 2026 – מגדל דוד מוזיאון"
    assert tower.source_genres == ["הופעה", "פסטיבל"]
    assert tower.venue_name == "מגדל דוד מוזיאון" and tower.city == "ירושלים" and tower.latitude is None
    radical = next(e for e in events.values() if e and "בית רדיקל" in e.title)
    assert radical.venue_name == "בית רדיקל" and radical.city == "תל אביב-יפו"
    home = next(e for e in events.values() if e and e.title.endswith("– צוללת צהובה"))
    assert home.venue_name == cfg.venue_name and home.latitude == cfg.latitude
