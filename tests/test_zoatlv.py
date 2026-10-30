"""ZOA House (zoatlv.co.il) parser against its real pages (October 2026)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.dedup import url_key
from partylinks.enrichment import Enricher
from partylinks.parsing import slugify
from partylinks.scrapers import zoatlv

FIXTURES = Path(__file__).parent / "fixtures" / "zoatlv"
TZ = "Asia/Jerusalem"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_zoatlv_listing_pages_and_sellers() -> None:
    settings = Settings()
    cfg = zoatlv.ZoaTlvSettings.model_validate(settings.sources["zoatlv"])
    listing = _read("events.html")
    assert zoatlv.last_page(listing) == 7
    cards = zoatlv.parse_listing(listing)
    assert len(cards) == 30
    events = [zoatlv.parse_card(c, cfg, TZ) for c in cards]
    assert all(events)

    keren = next(e for e in events if e and e.title == "דרור קרן | סטנדאפ")
    assert keren.start_at == datetime(2026, 10, 30, 21, 0, tzinfo=ZoneInfo(TZ))
    assert keren.latitude and keren.city == "תל אביב-יפו" and keren.venue_name == "בית ציוני אמריקה"
    assert keren.source_genres == ["הופעה", "סטנדאפ ובידור"] and (keren.extra or {})["hall"] == "מרתה"
    assert keren.images and keren.images[0].startswith("https://zoatlv.co.il/wp-content/")

    zoatlv.add_page(keren, _read("event.html"), cfg.seller_hosts)
    assert "בבקשה לא להגיע" in (keren.description or "")
    # The seller's link reads as Leaan's own listing URL, so dedup ties the two.
    leaan = settings.sources["leaan"]
    leaan_url = leaan["site_url"] + leaan["event_page_path"].format(slug=slugify("דרור קרן"), event_id=6998)
    assert url_key((keren.extra or {})["purchase_url"]) == url_key(str(type(keren.url)(leaan_url)))

    enriched = Enricher(settings.enrichment, TZ).enrich(keren)
    assert enriched and "stand_up" in enriched.genres and enriched.in_target_city

    # ZOA's own EASYTIX box office: the link points at easytix.co.il, where easytix lists it.
    workshop = next(e for e in events if e and e.title.startswith("לעלות על הבמה"))
    zoatlv.add_page(workshop, _read("event_easytix.html"), cfg.seller_hosts)
    assert (workshop.extra or {})["purchase_url"] == "https://easytix.co.il/event/ZOA-TLV/TAMAR"


def test_zoatlv_card_without_date_is_skipped() -> None:
    cfg = zoatlv.ZoaTlvSettings.model_validate(Settings().sources["zoatlv"])
    card = dict.fromkeys(("url", "title", "subtitle", "day", "month", "time", "hall", "tags", "image"), "")
    assert zoatlv.parse_card({**card, "url": "https://zoatlv.co.il/event/x/", "title": "x"}, cfg, TZ) is None
