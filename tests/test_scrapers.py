"""Parser tests against trimmed real payloads captured from each site."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers import eventer, goout, selector, tickchak, zygo

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
    deals = hulyo.list_deals(feed, cfg.areas)
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
    result = asyncio.run(scraper._with_artwork(event, "https://www.mevalim.co.il/central/"))  # type: ignore[attr-defined]
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
    assert asyncio.run(scraper._with_artwork(haifa, "https://www.mevalim.co.il/central/")).images == []  # type: ignore[attr-defined]
