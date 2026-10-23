"""Tel Aviv-Yafo municipality: the events service's items to ScrapedEvents."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.telaviv import TelAvivSettings, parse_items, search

TZ = "Asia/Jerusalem"
ITEMS = json.loads((Path(__file__).parent / "fixtures/telaviv/events.json").read_text("utf-8"))
SETTINGS = TelAvivSettings(
    cron="0 6 * * *", site_url="https://www.tel-aviv.gov.il",
    list_path="/_vti_bin/TlvSP2013PublicSite/TlvListUtils.svc/GetEvBenList", item_path="/Pages/MainItemPage.aspx",
    city="תל אביב-יפו",
)  # fmt: skip


def _events() -> dict[str, ScrapedEvent]:
    return {e.source_event_id: e for e in parse_items(ITEMS, SETTINGS, TZ)}


def test_free_event_with_place_picture_and_page() -> None:
    events = _events()
    assert len(events) == 7  # item 41434 is in both lists: two events
    film = events["969f7de3-48803"]
    assert film.title == "קולנוע הפסגה ביפו העתיקה" and film.start_at and film.end_at
    assert (film.start_at.isoformat(), film.end_at.isoformat()) == (
        "2026-10-28T21:00:00+02:00",
        "2026-10-28T23:00:00+02:00",
    )
    assert (film.venue_name, film.address, film.city) == ("גן הפסגה", "מפרץ שלמה 8", "תל אביב-יפו")
    assert film.organizer == "החברה לפיתוח יפו העתיקה"
    assert film.source_genres == ["תאטרון וקולנוע", "קולנוע", "קהילות", "אירועים קהילתיים"]
    assert film.images == ["https://www.tel-aviv.gov.il/digitelimages2020/a383caf2682f4fa69ddca11b903e0508.jpg"]
    assert str(film.url).endswith(
        "/Pages/MainItemPage.aspx?WebID=3af57d92-807c-43c5-8d5f-6fd455eb2776&ListID=969f7de3-0ac9-4a30-88f0-2e1a7281f3d0&ItemID=48803"
    )
    assert [(t.price, t.is_free) for t in film.tickets] == [(Decimal(0), True)] and film.min_price == 0
    assert film.description and film.description.startswith("מדי יום רביעי")


def test_prices_seller_link_and_runs() -> None:
    events = _events()
    paid = events["d7edbd99-40691"]
    assert [t.price for t in paid.tickets] == [Decimal(15), Decimal(30)]
    assert (paid.min_price, paid.max_price) == (Decimal(15), Decimal(30))
    assert paid.extra["purchase_url"] == "https://www.goshow.co.il/show/21957/57172"
    vague = events["d7edbd99-41578"]  # "אירוע בתשלום": paid, amount unknown
    assert not vague.tickets and vague.min_price is None
    partly = events["969f7de3-47820"]  # free entry, some paid parts; a season-long run
    assert [t.is_free for t in partly.tickets] == [True]
    assert partly.extra["purchase_url"].startswith("https://www.eventer.co.il/")
    assert partly.start_at and partly.end_at and (partly.end_at - partly.start_at).days > 200
    citywide = events["969f7de3-49787"]  # place "כמפורט בכתבה" points to the text
    assert citywide.venue_name is None


def test_search_window_is_utc_like_the_widget() -> None:
    body = search(SETTINGS, TZ)
    assert body["srch_dateStart"].endswith("T21:00:00.000Z") or body["srch_dateStart"].endswith("T22:00:00.000Z")
    assert body["srch_totalItemsLoad"] == 3000 and body["srch_free"] is False
