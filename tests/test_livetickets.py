"""LiveTickets parser tests against real listing pages (a city and the festivals page) and home links, trimmed."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.scrapers import livetickets

FIXTURES = Path(__file__).parent / "fixtures" / "livetickets"
SITE = "https://www.livetickets.co.il/"
EVENT_URL = SITE + "events/events.aspx?eid={eid}"


def _rows(name: str) -> list[livetickets.ScrapedEvent]:
    return livetickets.parse_rows((FIXTURES / name).read_text(encoding="utf-8"), EVENT_URL, "Asia/Jerusalem")


def test_livetickets_city_rows() -> None:
    match, tentative, concert = _rows("berlin.html")
    assert (match.source_event_id, match.title, str(match.url)) == (
        "2326352",
        "הרטה ברלין - ארמיניה בילפלד",
        SITE + "events/events.aspx?eid=2326352",
    )
    assert match.start_at == datetime(2026, 10, 24, 13, tzinfo=ZoneInfo("Europe/Berlin"))  # the venue's local time
    assert (match.venue_name, match.city, match.country) == ("האצטדיון האולימפי ברלין", "ברלין", "DE")
    assert (match.currency, match.min_price, match.extra) == ("EUR", Decimal(85), {})
    assert tentative.extra == {"date_not_final": True}
    assert concert.start_at is not None
    assert (concert.title, concert.start_at.month, concert.min_price) == ("Aespa", 1, Decimal(300))


def test_livetickets_festival_rows_dollar_and_no_price() -> None:
    tomorrowland, sziget = _rows("festivals.html")
    assert (tomorrowland.city, tomorrowland.country, tomorrowland.currency, tomorrowland.min_price) == (
        "Chon Buri",
        "TH",
        "USD",
        Decimal(690),
    )
    assert not tomorrowland.is_sold_out
    # Empty price box = out of stock (the event page shows only a "leave your details" form).
    assert (sziget.country, sziget.min_price, sziget.is_sold_out) == ("HU", None, True)


def test_livetickets_listing_links() -> None:
    links = livetickets.listing_links((FIXTURES / "home.html").read_text(encoding="utf-8"), SITE)
    # /search/ (robots-disallowed) and event links are not listings; the two casings of a page are one.
    assert sorted(links.values()) == [
        SITE + "Category1/CONCERTS.aspx",
        SITE + "DT_performers/Festivals.aspx",
        SITE + "Dt_performers/Hanuka.aspx",
        SITE + "cities/Germany_Berlin_Berlin.aspx",
        SITE + "performers/AC%20Milan.aspx",
    ]


def test_livetickets_category_names():
    assert livetickets.category_name("https://www.livetickets.co.il/Category1/CONCERTS.aspx") == "concerts"
    assert livetickets.category_name("https://www.livetickets.co.il/Category2/SOCCER.aspx") == "soccer"
    assert (
        livetickets.category_name("https://www.livetickets.co.il/Category3/English%20Premier%20League.aspx") == "soccer"
    )


def test_livetickets_performer_banner_is_the_picture() -> None:
    """The performer page's banner (as on livetickets.co.il/performers/Formula 1.aspx, Nov 2026) is its events'
    picture; the category's stock banner and city pages give none."""
    own = '<div class="h_search_section list" style="background-image: url(https://www.doctorticket.com/download/Heb_highlights/HebBackground_13368.png);">'
    stock = '<div class="h_search_section list" style="background-image: url(https://www.doctorticket.com/download/Heb_highlights/HebCatBackground_62.jpg);">'
    performer = SITE + "performers/Formula%201.aspx"
    image = livetickets.banner(own, performer)
    assert image == "https://www.doctorticket.com/download/Heb_highlights/HebBackground_13368.png"
    assert livetickets.banner(stock, SITE + "performers/Noah%20Kahan.aspx") is None
    assert livetickets.banner(own, SITE + "cities/Berlin.aspx") is None
    page = (FIXTURES / "berlin.html").read_text(encoding="utf-8")
    event = livetickets.parse_rows(page, EVENT_URL, "Asia/Jerusalem", image=image)[0]
    assert (event.image_url, event.images) == (image, [image])


def _row(eid: int, title: str) -> str:
    return (
        '<li class="list_tickets_item"><div class="tickets_date_year">2029</div>'
        '<div class="tickets_date_day">5 מרץ</div> 19:30 '
        f'<div class="tickets_name_txt">{title}</div><div class="tickets_place_txt">Apollo</div>'
        '<div class="tickets_place_txt">לונדון</div><div class="tickets_place_txt">בריטניה</div>'
        f'<a href="/events/events.aspx?eid={eid}">x</a></li>'
    )


def test_livetickets_reads_the_unlinked_categories_every_page_and_years_ahead(monkeypatch: Any) -> None:
    """The theatre category page (linked from no page, Nov 2026) is read from the config; its footer's popular
    performers get no "theatre"; a listing is read to its last page, asked for years ahead (the site stops at
    ~18 months by default)."""
    from partylinks.config import HttpSettings, Settings

    raw = {**(Settings().sources["livetickets"] or {}), "categories": ["Category1/THEATRE.aspx"]}
    scraper = livetickets.LiveTicketsScraper(livetickets.LiveTicketsSettings.model_validate(raw), HttpSettings(), "UTC")
    window = livetickets.window_query(datetime.now(ZoneInfo("UTC")), 10)
    assert window.startswith("?pn=0&EndDate=") and window.endswith("&PageNum=1")
    wicked = SITE + "performers/Wicked.aspx"
    pages = {
        SITE: f'<a class="footer_autolink" href="{SITE}performers/Billie%20Eilish.aspx">Billie</a>',
        SITE + "Category1/THEATRE.aspx": (
            f'<a class="category_list_link w-inline-block" href="/performers/Wicked.aspx">Wicked</a>'
            f'<a class="footer_autolink" href="{SITE}performers/Billie%20Eilish.aspx">Billie</a>'
            f'<a class="top_navlink_2 w-dropdown-link" href="{SITE}performers/FC%20Barcelona.aspx">FCB</a>'
        ),
        wicked + window: _row(1, "Wicked") + '<a id="hplNext" href="?pn=0&amp;PageNum=2">next</a>',
        wicked + "?pn=0&PageNum=2": _row(2, "Wicked") + f'<a id="hplNext" href="{window.replace("&", "&amp;")}">x</a>',
        SITE + "performers/Billie%20Eilish.aspx" + window: _row(3, "Billie Eilish"),
    }
    asked: list[str] = []

    async def get_text(url: str, **_: Any) -> str:
        asked.append(url)
        return pages[url]

    monkeypatch.setattr(scraper, "get_text", get_text)

    async def run() -> list[livetickets.ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(run())}
    assert sorted(events) == ["1", "2", "3"]  # page 2's next link back to page 1 ends the listing
    assert events["2"].source_genres == ["theatre"]
    assert events["3"].source_genres == []  # a footer link is no theatre
    assert events["1"].start_at == datetime(2029, 3, 5, 19, 30, tzinfo=ZoneInfo("Europe/London"))
    assert len(asked) == len(set(asked))
