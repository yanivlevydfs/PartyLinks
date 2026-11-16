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


def test_jerusalem_film_picture_from_its_own_site_at_the_crawl_delay() -> None:
    import asyncio
    from datetime import datetime, timedelta

    import httpx

    from partylinks.config import Settings
    from partylinks.scrapers import build_scraper
    from partylinks.scrapers.cinematheque_tlv import JerusalemCinemathequeScraper, film_nodes

    calendar = '<button class="toptix-purchase"\n data-event-id="43087"\n data-event-node="99048"\n>כרטיסים</button>'
    assert film_nodes(calendar) == {"43087": "99048"}
    day = (datetime.now() + timedelta(days=1)).date()
    row = dict(LISTING["presentations"][0]) | {"id": 43087, "dateTime": f"{day} 20:00", "businessDate": str(day)}
    scraper = build_scraper("cinematheque_jlm", Settings())
    assert isinstance(scraper, JerusalemCinemathequeScraper)
    scraper.http.request_delay_seconds = 0
    scraper.settings.crawl_delay_seconds = 0  # type: ignore[attr-defined]
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/presentations/":
            return httpx.Response(200, json={"presentations": [row]})
        if request.url.path == "/api/features/":
            return httpx.Response(200, json=[{"id": row["featureId"]}])  # PRES: no imageData
        if request.url.path.endswith("ticketGroups"):
            return httpx.Response(200, json={})
        if request.url.path == "/he":
            return httpx.Response(200, text=calendar, headers={"content-type": "text/html"})
        if request.url.path == "/he/event/99048":
            still = '<meta property="og:image" content="https://www.jer-cin.org.il/sites/default/files/still.jpg" />'
            return httpx.Response(200, text=still, headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    # Saved first without a picture, then once more with it (the same event, re-yielded).
    assert len(events) == 2 and events[0].source_event_id == events[1].source_event_id
    assert events[1].images == ["https://www.jer-cin.org.il/sites/default/files/still.jpg"]
    assert asked.count("/he/event/99048") == 1  # one film page per film


FESTIVAL_PAGE = """<ul>
<li data-filters="movie-cat-588" id="588" class="my-active"> סאונדטראק 2026 </li>
<li data-filters="movie-cat-597" id="597" class=""> פאנק </li>
<li data-filters="movie-cat-599" id="599" class=""> ישראלי </li>
</ul>"""


def festival_card(card_id: int, order_id: int, image_attr: str = "data-src") -> str:
    return f"""<div class="fest-box-parent-wrapper alm-listing event_id-{card_id} movie-cat-588">
<h4 class="by-date-title">24.11, שלישי</h4><div class="festival-grid-box"><div class="img-wraper">
<img {image_attr}="https://www.cinema.co.il/wp-content/uploads/2026/11/nofx.jpg" alt="img"
 src="data:image/svg+xml;base64,PHN2Zz4="></div><div class="text-content"><div class="title">
<h3><a href="https://www.cinema.co.il/event/nofx-2026/">NOFX | סאונדטראק 2026</a></h3></div>
<div class="text-left"><a href="https://www.cinema.co.il/event/nofx-2026/" class="underline">לפרטים</a>
<a href="https://cintlv.pres.global/order/{order_id}" class="order-btn">להזמנה</a></div></div></div></div>"""


def test_festival_cards_give_artwork_site_page_and_genres() -> None:
    import asyncio
    from datetime import datetime, timedelta
    from urllib.parse import parse_qs

    import httpx

    from partylinks.config import Settings
    from partylinks.enrichment import Enricher
    from partylinks.scrapers import build_scraper
    from partylinks.scrapers.cinematheque_tlv import (
        CinemathequeFestival,
        festival_sections,
        parse_festival_cards,
    )

    assert festival_sections(FESTIVAL_PAGE) == ("588", {"597": "פאנק", "599": "ישראלי"})
    lazy = parse_festival_cards(festival_card(1, 135449))["135449"]
    assert lazy.image == "https://www.cinema.co.il/wp-content/uploads/2026/11/nofx.jpg"  # not the data: placeholder
    assert lazy.url == "https://www.cinema.co.il/event/nofx-2026/"
    assert parse_festival_cards('<div class="alm-listing"><a class="order-btn" href="#">x</a></div>') == {}

    day = (datetime.now() + timedelta(days=1)).date()
    row = dict(LISTING["presentations"][0]) | {"id": 135449, "dateTime": f"{day} 20:00", "businessDate": str(day)}
    other = dict(row) | {"id": 135000, "featureId": 99999}  # not a festival screening
    settings = Settings()
    scraper = build_scraper("cinematheque_tlv", settings)
    scraper.http.request_delay_seconds = 0
    scraper.http.impersonate = None
    scraper.settings.festivals = [  # type: ignore[attr-defined]
        CinemathequeFestival(
            page_url="https://www.cinema.co.il/soundtracktelaviv2026/",
            genres=["פסטיבל"],
            section_genres={"פאנק": "punk"},
        )
    ]
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/presentations/":
            return httpx.Response(200, json={"presentations": [row, other]})
        if path == "/api/features/":
            return httpx.Response(200, json=[{"id": row["featureId"]}])  # PRES: no imageData
        if path.endswith("ticketGroups"):
            return httpx.Response(200, json={})
        if path == "/soundtracktelaviv2026/":
            return httpx.Response(200, text=FESTIVAL_PAGE, headers={"content-type": "text/html"})
        if path == "/wp-admin/admin-ajax.php":
            form = parse_qs(request.content.decode())
            forms.append(form)
            page, category = form["paged"][0], form["category_id"][0]
            html = festival_card(1, 135449) if page == "1" and category in ("588", "597") else ""
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    by_movie = {e.extra["movie_id"]: e for e in asyncio.run(go())}
    film, plain = by_movie[str(row["featureId"])], by_movie["99999"]
    assert str(film.url) == "https://www.cinema.co.il/event/nofx-2026/"
    assert film.extra["purchase_url"] == "https://cintlv.presglobal.store/order/135449"
    assert film.images == ["https://www.cinema.co.il/wp-content/uploads/2026/11/nofx.jpg"]
    assert film.source_genres == ["קולנוע", "פסטיבל", "punk"]  # "ישראלי" is not mapped
    assert film.extra["series"][-1] == "פאנק"
    assert str(plain.url).startswith("https://cintlv.presglobal.store/order/") and "פסטיבל" not in plain.source_genres
    # Each category paged until an empty answer: 588 (1, 2), 597 (1, 2), 599 (1).
    assert [(f["category_id"][0], f["paged"][0]) for f in forms] == [
        ("588", "1"), ("588", "2"), ("597", "1"), ("597", "2"), ("599", "1"),
    ]  # fmt: skip
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(film)
    assert enriched is not None and {"cinema", "festival", "punk"} <= set(enriched.genres)
