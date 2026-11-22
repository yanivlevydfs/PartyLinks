"""Jerusalem Arts Festival (jerusalemarts.co.il): wp-json posts, each with its schema.org Event -> shows.

Fixtures (22.11.2026): the 15 posts of the 2026 festival (2-3.12) as wp-json serves them (only the fields
read: id, link, title, content with its ``jaf-ld`` Event, tags) and the tag names.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.jerusalemarts import JerusalemArtsScraper, JerusalemArtsSettings, parse_post, post_event

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "jerusalemarts"
POSTS = {p["id"]: p for p in json.loads((FIXTURES / "posts.json").read_text("utf-8"))}
TAGS = {t["id"]: t["name"] for t in json.loads((FIXTURES / "tags.json").read_text("utf-8"))}
CAMPUS = (31.7797237, 35.2125829)  # מנורה 3 (OSM), the Arts Campus's street
NOCTURNO = (31.7804823, 35.2138317)  # Nocturno café (OSM, amenity=cafe), בצלאל 7
# As in the config block.
CFG = JerusalemArtsSettings.model_validate({
    "cron": "0 0 * * *",
    "places": {
        "מנורה 3": {"venue_name": "קמפוס האמנויות ירושלים", "latitude": CAMPUS[0], "longitude": CAMPUS[1]},
        "בצלאל 7": {"venue_name": "נוקטורנו", "latitude": NOCTURNO[0], "longitude": NOCTURNO[1]},
    },
})  # fmt: skip


def _show(post_id: int) -> ScrapedEvent:
    event = parse_post(POSTS[post_id], TAGS, CFG, TZ)
    assert event is not None
    return event


def test_paid_show_at_a_campus_hall() -> None:
    show = _show(2808)
    assert (show.source, show.source_event_id) == ("jerusalemarts", "2808")
    assert show.title == "R U OK?"  # the festival's name after it is dropped
    assert str(show.url) == "https://jerusalemarts.co.il/ruok/"
    assert show.start_at == datetime(2026, 12, 2, 19, 0, tzinfo=ZoneInfo(TZ))
    assert show.end_at == datetime(2026, 12, 2, 19, 55, tzinfo=ZoneInfo(TZ))
    # The hall is one of the campus's: one venue, the hall kept beside it.
    assert (show.venue_name, show.address, show.city) == ("קמפוס האמנויות ירושלים", "מנורה 3, ירושלים", "ירושלים")
    assert (show.latitude, show.longitude) == CAMPUS
    assert show.extra == {
        "purchase_url": "https://www.eventer.co.il/48l4f",
        "hall": "אולם מריו, בית הספר לתיאטרון חזותי",
        "performer": "תיאטרון הגברת",
    }
    assert show.source_genres == ["פסטיבל", "פרפורמנס", "תיאטרון"]
    assert [(t.name, t.price, t.is_free) for t in show.tickets] == [("כרטיס", Decimal(60), False)]
    assert (show.min_price, show.max_price) == (Decimal(60), Decimal(60))
    assert show.images == [
        "https://jerusalemarts.co.il/wp-content/uploads/2026/11/SIX_30DE1B27-764E-4B87-BC3F-95BA40374F2C-2-scaled.jpg"
    ]
    assert show.description and "אורנת למפרט" in show.description
    assert "@theladytheatre" not in show.description  # the Instagram line is not the description
    assert show.organizer == "פסטיבל ירושלים לאמנויות"
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and {"festival", "theatre"} <= set(enriched.genres)


def test_free_show_has_no_seller_and_another_building() -> None:
    free = _show(2820)  # The Joon Orchestra: its offer points back at the festival site
    assert free.title == "The Joon Orchestra"
    assert "purchase_url" not in free.extra
    assert [(t.price, t.is_free) for t in free.tickets] == [(Decimal(0), True)] and free.min_price == 0
    nocturno = _show(2824)  # יוצר לאור, at Nocturno (בצלאל 7)
    assert (nocturno.venue_name, (nocturno.latitude, nocturno.longitude)) == ("נוקטורנו", NOCTURNO)
    assert "hall" not in nocturno.extra  # "נוקטורנו, בצלאל 7" is the venue itself
    assert _show(2828).extra["purchase_url"] == "https://tickchak.co.il/116929"  # sold on Tickchak
    # A post with no tags still has the festival genre.
    cafe = _show(3232)
    assert cafe.source_genres == ["פסטיבל"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(cafe)
    assert enriched is not None and "festival" in enriched.genres


def test_every_post_is_a_show_with_artwork_and_a_title() -> None:
    shows = [parse_post(p, TAGS, CFG, TZ) for p in POSTS.values()]
    assert all(shows) and len(shows) == 15
    assert all(s and s.images and s.latitude and s.title and "פסטיבל ירושלים" not in s.title for s in shows)
    titles = {s.title for s in shows if s}
    assert "דיג׳י מש – הכל על האש – טרום השקת אלבום" in titles
    no_end = _show(3374)  # הלוויה חורפית: the Event gives no end
    assert no_end.end_at is None
    # A Hebrew file name is percent-encoded.
    assert _show(2828).images[0].startswith("https://jerusalemarts.co.il/wp-content/uploads/2026/11/%D7%93")


def test_posts_without_an_event_or_start_are_skipped() -> None:
    post = POSTS[2808]
    assert parse_post({**post, "content": {"rendered": "<p>בקרוב</p>"}}, TAGS, CFG, TZ) is None
    event = post_event(post["content"]["rendered"])
    assert event is not None
    broken = post["content"]["rendered"].replace(event["startDate"], "soon")
    assert parse_post({**post, "content": {"rendered": broken}}, TAGS, CFG, TZ) is None
    assert parse_post({**post, "link": ""}, TAGS, CFG, TZ) is None
    # A street not in places: the hall is the venue, its address for geocoding.
    elsewhere = post["content"]["rendered"].replace("מנורה 3", "יפו 97")
    show = parse_post({**post, "content": {"rendered": elsewhere}}, TAGS, CFG, TZ)
    assert show is not None
    assert (show.venue_name, show.address, show.latitude) == (
        "אולם מריו, בית הספר לתיאטרון חזותי",
        "יפו 97, ירושלים",
        None,
    )


def test_run_reads_posts_and_tags() -> None:
    scraper = build_scraper("jerusalemarts", Settings())
    assert isinstance(scraper, JerusalemArtsScraper)
    assert scraper.settings.places and scraper.settings.skip_title_words
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in TAGS.items()])
        return httpx.Response(200, json=list(POSTS.values()), headers={"X-WP-Total": str(len(POSTS))})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert sorted(asked) == ["/wp-json/wp/v2/posts", "/wp-json/wp/v2/tags"]  # two requests a run
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past shows (once the festival is over) are left out
    assert len(events) <= 15 and not scraper.failures
