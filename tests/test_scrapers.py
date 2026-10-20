"""Parser tests against trimmed real payloads captured from each site."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.artists import ArtistBook, ArtistProfile
from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers import barby, eventer, goout, grayclub, hulyo, mevalim, selector, tickchak, zygo

FIXTURES = Path(__file__).parent / "fixtures"
TZ = "Asia/Jerusalem"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_goout_reads_every_ticket_tier_with_rounds_fee_and_status() -> None:
    templates = ["https://images.go-out.co/events/{event_id}{cover_timestamp}_coverImage.jpg"]
    event = goout.parse_event(
        load("goout_event"), "https://www.go-out.co/event/1792399854329", TZ, load("goout_tickets"), templates
    )

    assert event.source_event_id == "6ad5d9db4b18d81c2451751b"
    assert event.start_at is not None and event.start_at.utcoffset() is not None
    assert [(t.name, t.price, t.status) for t in event.tickets] == [
        ("כרטיסים מוזלים למקדימים", Decimal(15), TierStatus.SOLD_OUT),
        ("כרטיס כניסה", Decimal(30), TierStatus.ON_SALE),
        ("הזדמנות אחרונה", Decimal(50), TierStatus.ON_SALE),
    ]
    entry = event.tickets[1]
    assert entry.service_fee == Decimal("1.50") and entry.total_price == Decimal("31.50")
    assert entry.kind is TicketKind.PAID
    assert event.min_price == Decimal(30) and event.max_price == Decimal(50)
    assert event.image_url == "https://images.go-out.co/events/6ad5d9db4b18d81c2451751b1792403787340_coverImage.jpg"


def test_goout_falls_back_to_schema_offers_without_ticket_api() -> None:
    event = goout.parse_event(load("goout_event"), "https://www.go-out.co/event/1792399854329", TZ, {"status": False})
    assert event.tickets and all(t.price is not None for t in event.tickets)


def test_eventer_free_and_paid_tiers() -> None:
    event = eventer.parse_event(load("eventer_event"), load("eventer_tickets"), "https://www.eventer.co.il/m302f", TZ)

    kinds = {t.kind for t in event.tickets}
    assert kinds == {TicketKind.FREE, TicketKind.PAID}
    assert event.min_price == 0 and event.max_price == Decimal(109)
    assert event.venue_name and event.city and event.latitude
    assert len(event.images) >= 2


def test_zygo_tickets_and_location() -> None:
    event = zygo.parse_event(load("zygo_event"), "https://zygo.co.il/event/x/zygo")

    assert event.tickets and all(t.price is not None for t in event.tickets)
    assert event.city == "Tel Aviv-Yafo"
    assert event.description and "<" not in event.description


def test_tickchak_section_items() -> None:
    items = load("tickchak_section")["events"]
    events = [tickchak.parse_event(item, "https://live.tickchak.co.il") for item in items]

    assert events and all(e.url.host == "live.tickchak.co.il" for e in events)
    assert all(e.min_price is not None and e.extra["provider"] for e in events)


def test_tickchak_artist_profiles_merge_into_artist_book(tmp_path: Path) -> None:
    items = load("tickchak_section")["events"]
    profiles = [p for item in items for p in tickchak.parse_event(item, "https://live.tickchak.co.il").artist_profiles]
    balishag = next(p for p in profiles if p.name == "Balishag")
    assert balishag.profile_url == "https://live.tickchak.co.il/balishag"
    assert balishag.spotify_url and "spotify.com" in balishag.spotify_url

    book = ArtistBook(tmp_path / "artists.db")
    book.save_profiles([ArtistProfile("Balishag", "goout", image_url="https://img/b.jpg")])
    book.save_profiles([balishag])  # a sparser profile from another source keeps the photo
    stored = book.profiles()["balishag"]
    assert stored.image_url == "https://img/b.jpg" and stored.spotify_url == balishag.spotify_url


def test_tickchak_discovers_sections_from_page_layout() -> None:
    page = {
        "pageDesign": {
            "sections": [
                {"sectionId": 2, "type": "promotedSlider"},
                {"sectionId": 4, "type": "eventSlider"},
                {"sectionId": 5, "type": "loopSections", "content": "event"},
                {"sectionId": 6, "type": "artistSlider"},
            ]
        },
        "sectionsData": {"loopSections": {"5": {"sliders": [{"id": "thisWeek"}, {"id": "weekend"}]}}},
    }
    assert tickchak.discover_section_ids(page) == ["4", "5_thisWeek", "5_weekend"]


def test_selector_ticket_tiers() -> None:
    event = selector.parse_event(load("selector_event"), "https://selector.org.il/e/{event_link}")

    assert str(event.url).startswith("https://selector.org.il/e/")
    assert event.tickets
    assert all(t.admits >= 1 and t.price is not None for t in event.tickets)


def test_barby_sequential_tiers_and_single_price() -> None:
    from partylinks.config import Settings
    from partylinks.scrapers import barby

    cfg = barby.BarbySettings.model_validate(Settings().sources["barby"])
    shows = load("barby_shows")

    rounds = barby.parse_event(**{k: shows["2"][k] for k in ("listed", "details", "tiers")}, s=cfg, timezone=TZ)
    assert [t.status for t in rounds.tickets] == [TierStatus.SOLD_OUT, TierStatus.ON_SALE]
    assert rounds.tickets[0].price_rounds == [85.0, 105.0]
    assert rounds.min_price == Decimal(105) and rounds.city and rounds.images

    single = barby.parse_event(**{k: shows["1"][k] for k in ("listed", "details", "tiers")}, s=cfg, timezone=TZ)
    assert len(single.tickets) == 1 and single.tickets[0].price == Decimal(245)


def test_grayclub_cards_and_event_page() -> None:
    from partylinks.config import Settings
    from partylinks.scrapers import grayclub

    cfg = grayclub.GrayclubSettings.model_validate(Settings().sources["grayclub"])
    cards = grayclub.parse_cards((FIXTURES / "grayclub_cards.html").read_text(encoding="utf-8"), cfg.selectors)
    assert len(cards) == 3 and all("/event/" in c["url"] for c in cards)

    page = (FIXTURES / "grayclub_event.html").read_text(encoding="utf-8")
    event = grayclub.parse_event(page, cards[0], cfg, TZ)
    assert event.title and event.description
    assert event.start_at is not None and (event.start_at.hour, event.start_at.minute) == (21, 0)
    assert event.min_price == Decimal(98) and event.tickets[0].kind is TicketKind.PAID
    assert event.address and event.extra["purchase_url"] and event.images


def test_hulyo_feed_filters_areas_and_merges_promoted() -> None:
    from partylinks.config import Settings
    from partylinks.scrapers import hulyo

    cfg = hulyo.HulyoSettings.model_validate(Settings().sources["hulyo"])
    feed = load("hulyo_shows")
    assert len(hulyo.list_deals(feed, [])) == 3
    assert cfg.areas == []  # every area
    deals = hulyo.list_deals(feed, ["תל אביב והמרכז"])
    assert len(deals) == 1 and deals[0]["areaName"] == "תל אביב והמרכז"

    event = hulyo.parse_event(deals[0], cfg, TZ)
    assert event.title and event.venue_name and event.address
    assert event.start_at is not None and event.start_at.utcoffset() is not None
    assert event.min_price is not None and event.tickets[0].kind is TicketKind.PAID
    assert f"id={deals[0]['id']}" in str(event.url)


def test_mevalim_rows_deal_price_and_sold_out() -> None:
    from partylinks.scrapers import mevalim

    rows = mevalim.parse_rows((FIXTURES / "mevalim_rows.html").read_text(encoding="utf-8"))
    region = "https://www.mevalim.co.il/central/"
    on_sale, sold = (mevalim.parse_row(r, region, TZ) for r in rows)

    assert on_sale.source_event_id == "131652" and on_sale.title == "חידון חברים"
    assert (on_sale.venue_name, on_sale.city) == ("ספוט הוסטל", "תל אביב")
    assert on_sale.start_at is not None and (on_sale.start_at.day, on_sale.start_at.hour) == (20, 21)
    assert on_sale.min_price == Decimal(54) and on_sale.extra["original_price"] == 66
    assert str(on_sale.url) == "https://tickets.mevalim.co.il/event/dV8dtbEB15BVjYo"  # tracking query dropped
    assert not on_sale.is_sold_out

    assert sold.is_sold_out and str(sold.url) == region and sold.min_price is None


def test_mevalim_artwork_from_event_page() -> None:
    from partylinks.scrapers import mevalim

    # Shape of a tickets.mevalim.co.il event page (Next.js): optimizer <img> + og:image.
    page = (
        '<html><head><meta property="og:image" content="https://tickets-api.mevalim.co.il/up/social.jpg">'
        '</head><body><img alt="" src="/_next/image?url=https%3A%2F%2Ftickets-api.mevalim.co.il%2Fup%2Fmain.jpeg'
        '&amp;w=1920&amp;q=75"></body></html>'
    )
    assert mevalim.parse_artwork(page) == [
        "https://tickets-api.mevalim.co.il/up/main.jpeg",
        "https://tickets-api.mevalim.co.il/up/social.jpg",
    ]
    assert mevalim.parse_artwork("<html><body>no artwork</body></html>") == []


def test_mevalim_reuses_stored_artwork_without_a_request() -> None:
    import asyncio

    from partylinks.config import Settings
    from partylinks.models import ScrapedEvent
    from partylinks.scrapers import build_scraper

    scraper = build_scraper("mevalim", Settings())
    scraper.known_images = {"1": ["https://img.example/stored.jpg"]}

    async def no_network(*args: object, **kwargs: object) -> str:
        raise AssertionError("an event with stored artwork must not be fetched")

    scraper.get_text = no_network  # type: ignore[method-assign]
    event = ScrapedEvent(source="mevalim", source_event_id="1", url="https://tickets.example/event/1", title="Show")
    result = asyncio.run(scraper._with_artwork(event))  # type: ignore[attr-defined]
    assert result.images == ["https://img.example/stored.jpg"]


def test_mevalim_skips_artwork_for_events_that_will_be_dropped() -> None:
    import asyncio

    from partylinks.config import Settings
    from partylinks.models import ScrapedEvent
    from partylinks.scrapers import build_scraper

    scraper = build_scraper("mevalim", Settings())
    scraper.wanted = lambda event: event.city == "תל אביב"

    async def no_network(*args: object, **kwargs: object) -> str:
        raise AssertionError("an event outside the target city must not be fetched")

    scraper.get_text = no_network  # type: ignore[method-assign]
    haifa = ScrapedEvent(source="mevalim", source_event_id="9", url="https://tickets.example/event/9", title="X", city="חיפה")
    assert asyncio.run(scraper._with_artwork(haifa)).images == []  # type: ignore[attr-defined]


def test_eventer_artist_catalog_keeps_both_spellings_and_links() -> None:
    slide = load("eventer_artists")["slides"][0]
    profiles = eventer.parse_artist(slide, load("eventer_artist"), "https://www.eventer.co.il/artists/{link_name}")
    assert [p.name for p in profiles] == ["בוריס ברצ'ה", "Boris Brejcha"]
    boris = profiles[1]
    assert boris.profile_url == "https://www.eventer.co.il/artists/boris_brejcha"
    assert boris.image_url and boris.image_url.startswith("https://images.eventer.co.il/")
    assert "https://www.instagram.com/borisbrejcha" in boris.links
    # Catalog only (artist already known): photo and page, no links.
    assert eventer.parse_artist(slide, {}, "https://e/{link_name}")[1].links == []


def test_mevalim_artist_pages_drop_placeholder_photos() -> None:
    pages = load("mevalim_artist_pages")
    photos = {695988: "https://www.mevalim.co.il/wp-content/uploads/a.jpg"}  # 115240 (placeholder) not resolved
    profiles = [p for page in pages if (p := mevalim.parse_artist_page(page, "singer", photos))]
    assert [p.name for p in profiles] == ["עופר ניסים", "שרים החלונות הגבוהים", "האחים זו-ארץ"]
    assert profiles[0].image_url is None and profiles[1].image_url == photos[695988]
    assert profiles[0].kind == "singer" and profiles[0].profile_url.startswith("https://www.mevalim.co.il/shows/")
    assert mevalim.parse_artist_page({"title": {"rendered": "הופעות חיות בחיפה"}}, "singer", {}) is None


def test_barby_show_name_gives_headliner_and_guests() -> None:
    cases = {
        "איתי לוי - בבארבי": ["איתי לוי"],
        "ויתרתי VS כוח וידאו": ["ויתרתי", "כוח וידאו"],
        "אקו x מיצי: THE NEW ACT!": ["אקו", "מיצי"],
        "רונה קינן - במופע להקה חדש! אורחים :שלומי שבן , ערן צור.": ["רונה קינן", "שלומי שבן", "ערן צור"],
        "זהבה בן-חוגגת יום הולדת עם אורחים מיוחדים": ["זהבה בן"],
        "מסיבת העשורים הגדולה - ריקודים": [],
        "מופע המחווה ל-Avenged Sevenfold": [],
    }
    for name, expected in cases.items():
        assert barby.show_artists(name, "") == expected, name
    assert barby.show_artists("אריאל זילבר בבארבי- 50 שנה", " חיים רומנו ושמוליק בודגוב") == [
        "אריאל זילבר", "חיים רומנו", "שמוליק בודגוב",
    ]


def test_hulyo_deal_title_gives_the_star_only() -> None:
    assert hulyo.deal_artists("נחום דידי במופע סטנד  אפ") == ["נחום דידי"]
    assert hulyo.deal_artists("איש הגשם- בכיכובם של דביר בנדק ודן שפירא") == ["דביר בנדק", "דן שפירא"]
    assert hulyo.deal_artists('שירים מהסרטים" מירי חן מארחת את משה להב ') == ["מירי חן", "משה להב"]
    assert hulyo.deal_artists("Dancing Queen - מופע מחווה ללהקת ABBA") == []
    assert hulyo.deal_artists("גרושים") == []  # a play


def test_grayclub_artist_page_profile() -> None:
    page = (FIXTURES / "grayclub_artist.html").read_text(encoding="utf-8")
    profile = grayclub.parse_artist_page(page, "https://grayclub.co.il/shows-artist/x/", grayclub.GrayArtistSelectors())
    assert profile and profile.name == "שלומית אהרון"
    assert profile.image_url and profile.image_url.endswith(".jpg")
    assert any("instagram.com" in link for link in profile.links)
