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


def test_cinematheque_jlm_reads_the_whole_feed_and_maps_its_categories() -> None:
    from partylinks.config import RetentionSettings, Settings
    from partylinks.enrichment import Enricher
    from partylinks.scrapers import build_scraper

    settings = Settings()
    s = build_scraper("cinematheque_jlm", settings).settings
    assert isinstance(s, TelAvivCinemathequeSettings)
    # The feed lists months ahead (Met Opera to 05.06.2027 on 23.11.2026): no window shorter than retention's.
    assert s.days_ahead >= RetentionSettings().max_future_days
    base = LISTING["presentations"][0]
    shows = [  # (feature, PRES category, day) as jer-cin.pres.global lists them
        (18479, "קורסים", "2026-11-27"),  # a lecture-course meeting, sold one by one
        (18408, "מטרופוליטן", "2027-06-05"),  # 194 days ahead
        (19024, "national theatre", "2026-12-26"),
        (19082, "ילדים", "2026-12-07"),
        (19101, "פסטיבל יהודי", "2026-12-05"),
        (19027, "קונצ'רטו", "2026-12-05"),  # a short concert, a lecture and a film
        (19065, "סרטים", "2026-11-28"),
    ]
    rows = [
        dict(base) | {"id": i, "featureId": f, "featureCategoryName": c, "dateTime": f"{d} 19:00", "businessDate": d}
        for i, (f, c, d) in enumerate(shows, 1)
    ]
    groups = group_screenings({"presentations": rows}, TZ, date(2026, 11, 23), s.days_ahead)
    built = {m: build_event(m, d, x, {}, [], s, "cinematheque_jlm") for (m, d), x in groups.items()}
    assert set(built) == {str(f) for f, _, _ in shows}
    enricher = Enricher(settings.enrichment, settings.timezone)
    enriched = {m: enricher.enrich(e) for m, e in built.items()}
    genres = {m: e.genres for m, e in enriched.items() if e is not None}
    assert set(genres) == set(built)
    assert built["18479"].source_genres == ["הרצאה", "קולנוע"] and "lectures" in genres["18479"]
    assert {"cinema", "classical_opera"} <= set(genres["18408"])
    assert {"cinema", "theatre"} <= set(genres["19024"])
    assert {"cinema", "kids"} <= set(genres["19082"])
    assert {"cinema", "festival"} <= set(genres["19101"])
    assert {"cinema", "lectures", "live_show"} <= set(genres["19027"])
    assert built["19065"].source_genres == ["קולנוע"] and built["19065"].extra["series"] == ["סרטים"]


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


def test_movie_days_soonest_first() -> None:
    rows = [
        {"id": 1, "featureId": "a", "dateTime": "2026-10-24T20:00:00", "businessDate": "2026-10-24"},
        {"id": 2, "featureId": "b", "dateTime": "2026-10-22T21:00:00", "businessDate": "2026-10-22"},
        {"id": 3, "featureId": "a", "dateTime": "2026-10-22T18:00:00", "businessDate": "2026-10-22"},
    ]
    groups = group_screenings({"presentations": rows}, TZ, date(2026, 10, 22), 7)
    assert list(groups) == [("a", date(2026, 10, 22)), ("b", date(2026, 10, 22)), ("a", date(2026, 10, 24))]


def test_whole_feed_read_nearest_first_with_prices_per_batch_and_over_screenings_dropped() -> None:
    import asyncio
    from datetime import datetime, time, timedelta

    import httpx

    from partylinks.config import Settings
    from partylinks.parsing import zone
    from partylinks.scrapers import build_scraper

    now = datetime.now(zone(TZ)).replace(tzinfo=None)
    base = LISTING["presentations"][0]

    def row(rid: int, feature: int, start: datetime, minutes: int = 100, day: date | None = None) -> dict:
        when = {"dateTime": start.strftime("%Y-%m-%d %H:%M"), "businessDate": str(day or start.date())}
        return dict(base) | {"id": rid, "featureId": feature, "durationInMinutes": minutes} | when

    tomorrow = datetime.combine(now.date() + timedelta(days=1), time(20, 0))
    far = tomorrow + timedelta(days=200)  # beyond any short window: the whole feed is read
    rows = [
        row(3, 300, far + timedelta(hours=1)),
        row(2, 200, far),
        row(1, 100, tomorrow),
        row(4, 100, tomorrow + timedelta(days=1)),  # the same film's next day: its price is not asked again
        row(5, 400, now - timedelta(hours=3), minutes=1, day=now.date()),  # today's only screening, over
    ]
    scraper = build_scraper("cinematheque_tlv", Settings())
    scraper.http.request_delay_seconds = 0
    scraper.http.impersonate = None
    scraper.settings.festivals = []  # type: ignore[attr-defined]
    scraper.settings.days_ahead = 730  # type: ignore[attr-defined]
    scraper.settings.price_batch = 1  # type: ignore[attr-defined]
    log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/presentations/":
            return httpx.Response(200, json={"presentations": rows})
        if path == "/api/features/":
            return httpx.Response(200, json=[])
        if path.endswith("ticketGroups"):
            log.append("price " + path.split("/")[3])
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async def go() -> None:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async for event in scraper.scrape():
                log.append("event " + event.extra["movie_id"])

    asyncio.run(go())
    # Each batch's prices just before its events, soonest first; the over screening of today is never yielded.
    assert log == ["price 5", "price 1", "event 100", "event 100", "price 2", "event 200", "price 3", "event 300"]


HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]  # Python's weekday() order
PLACEHOLDER = "https://www.cinema.co.il/wp-content/themes/cinematheque_new_theme/images/event_d2_new.png"


def free_card(post_id: int, day: date, text: str, image: str = PLACEHOLDER, minutes: int = 180) -> str:
    """A festival card with no order button, as /solidarity2026/'s load_more_posts gives it (Nov 2026)."""
    return f"""<div class="fest-box-parent-wrapper alm-listing event_id-{post_id} movie-cat-623">
<h4 class="by-date-title">{day:%d.%m}, {HEBREW_DAYS[day.weekday()]}</h4><div class="festival-grid-box">
<div class="img-wraper"><img src="{image}" alt="img"></div><div class="text-content"><ul></ul><div class="title">
<h3 class="21852"><a href="https://www.cinema.co.il/event/free-{post_id}/">סולידריות עכשיו | סולידריות 2026</a></h3>
<p> אורך:{minutes}</p></div><div class="paragraph"> {text} </div><div class="content-detail"><div class="text-left">
<a href="https://www.cinema.co.il/event/free-{post_id}/" class="underline">לפרטים ותאריכים נוספים</a></div></div>
</div></div></div>"""


def test_free_festival_cards_are_events_of_their_own() -> None:
    from partylinks.scrapers.cinematheque_tlv import build_free_event, parse_festival_cards, parse_free_cards

    today = date(2026, 11, 23)
    upload = "https://www.cinema.co.il/wp-content/uploads/2026/11/solidarity-2026-21849.jpg"
    cards = parse_free_cards(
        free_card(119841, date(2026, 12, 10), "סולידריות עכשיו כניסה חופשית – יום ה׳ 10.12, 15:00, אולם 6")
        + free_card(119853, date(2026, 12, 12), "בתום ההקרנה תתקיים שיחה כניסה חופשית – יום ש׳...", upload, 150)
        + festival_card(1, 135449)  # sold by PRES: not a free card
    )
    assert set(cards) == {"119841", "119853"}
    now, cut = cards["119841"], cards["119853"]
    assert now.image is None and cut.image == upload  # the theme's "no picture" image is no artwork
    assert parse_festival_cards(festival_card(1, 135449, image_attr="src").replace(
        "https://www.cinema.co.il/wp-content/uploads/2026/11/nofx.jpg", PLACEHOLDER
    ))["135449"].image is None  # fmt: skip
    now.genres = ["פסטיבל", "קולנוע"]
    event = build_free_event(now, today, SETTINGS, "cinematheque_tlv", TZ)
    assert event is not None and event.start_at is not None and event.end_at is not None
    assert (event.start_at.isoformat(), event.end_at.isoformat()) == (
        "2026-12-10T15:00:00+02:00",
        "2026-12-10T18:00:00+02:00",
    )
    assert event.time_known and event.title == "סולידריות עכשיו | סולידריות 2026"
    assert (
        str(event.url) == "https://www.cinema.co.il/event/free-119841/"
        and event.source_event_id == "card119841:tlv:2026-12-10"
    )
    assert event.min_price == 0 and event.tickets[0].is_free and event.extra["halls"] == ["אולם 6"]
    assert event.source_genres == ["פסטיבל", "קולנוע"] and event.venue_name == "סינמטק תל אביב"
    # The card's text is cut before the hour: all-day until its own page gives it (not the menu's "היום 20:45").
    bare = build_free_event(cut, today, SETTINGS, "cinematheque_tlv", TZ)
    assert bare is not None and not bare.time_known and bare.end_at is None
    page = "<div>23.11.26 היום 20:45 מתוך אהבה</div><p>כניסה חופשית – יום ש׳ 12.12, 10:00, אולם 2</p>"
    timed = build_free_event(cut, today, SETTINGS, "cinematheque_tlv", TZ, page)
    assert timed is not None and timed.start_at is not None and timed.time_known
    assert timed.start_at.isoformat() == "2026-12-12T10:00:00+02:00" and timed.extra["halls"] == ["אולם 2"]
    # The year is the nearest one on the card's weekday: in late December, 05.01 (a Tuesday) is next January.
    january = parse_free_cards(free_card(7, date(2027, 1, 5), "כניסה חופשית – 05.01, 18:00, אולם 1"))["7"]
    late = build_free_event(january, date(2026, 12, 30), SETTINGS, "cinematheque_tlv", TZ)
    assert late is not None and late.start_at is not None and late.start_at.date() == date(2027, 1, 5)


def test_free_festival_events_yielded_in_date_order_and_paging_goes_past_free_cards() -> None:
    import asyncio
    from datetime import datetime, timedelta
    from urllib.parse import parse_qs

    import httpx

    from partylinks.config import Settings
    from partylinks.scrapers import build_scraper
    from partylinks.scrapers.cinematheque_tlv import CinemathequeFestival

    today = datetime.now().date()
    d1, d2, d3 = (today + timedelta(days=n) for n in (1, 2, 3))
    base = LISTING["presentations"][0]
    rows = [
        dict(base) | {"id": 135449, "featureId": 1, "dateTime": f"{d1} 20:00", "businessDate": str(d1)},
        dict(base) | {"id": 135450, "featureId": 2, "dateTime": f"{d3} 20:00", "businessDate": str(d3)},
    ]
    pages = {  # page 1 holds free cards only: the next page is still read
        "1": free_card(5, d2, f"כניסה חופשית – {d2:%d.%m}, 15:00, אולם 6") + free_card(6, d2, "כניסה חופשית..."),
        "2": festival_card(1, 135449),
    }
    scraper = build_scraper("cinematheque_tlv", Settings())
    scraper.http.request_delay_seconds = 0
    scraper.http.impersonate = None
    scraper.settings.festivals = [CinemathequeFestival(page_url="https://www.cinema.co.il/solidarity2026/")]  # type: ignore[attr-defined]
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/api/presentations/":
            return httpx.Response(200, json={"presentations": rows})
        if path in ("/api/features/",) or path.endswith("ticketGroups"):
            return httpx.Response(200, json=[] if path == "/api/features/" else {})
        if path == "/solidarity2026/":
            page = '<li data-filters="movie-cat-623" class="my-active"> סולידריות 2026 </li>'
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if path == "/wp-admin/admin-ajax.php":
            paged = parse_qs(request.content.decode())["paged"][0]
            return httpx.Response(200, text=pages.get(paged, ""), headers={"content-type": "text/html"})
        if path == "/event/free-6/":
            return httpx.Response(
                200, text=f"<p>כניסה חופשית – {d2:%d.%m}, 11:00, אולם 2</p>", headers={"content-type": "text/html"}
            )
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert [(e.extra.get("movie_id") or e.extra["festival_card"], e.start_at and e.start_at.strftime("%H:%M")) for e in events] == [
        ("1", "20:00"), ("6", "11:00"), ("5", "15:00"), ("2", "20:00"),
    ]  # fmt: skip
    assert str(events[0].url) == "https://www.cinema.co.il/event/nofx-2026/"  # the paid card found on page 2
    assert asked.count("/event/free-6/") == 1 and "/event/free-5/" not in asked  # only the card without its hour
    assert all(e.source_genres == ["פסטיבל"] for e in events[1:3])
