"""Nefesh B'Nefesh (nbn.org.il): MEC's iCal feed, then each upcoming event's page.

The entries are real (feed of 23.11.2026, descriptions cut): a singles mixer at a Tel Aviv cafe, a weekly chess
club in Herzliya (RRULE), a Zoom lecture series (RDATE), an all-day workshop on four dates (RDATE of dates) and a
comedy tour with no place. The page is the mixer's, cut to its JSON-LD and the MEC cost / booking markup.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import zone
from partylinks.scrapers import build_scraper
from partylinks.scrapers.nbn import NbnScraper, feed_events, parse_vevent, with_page

TZ = "Asia/Jerusalem"
IL = zone(TZ)
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=IL)

HEAD = """<br />
<b>Warning</b>:  finfo_file(deploy-tlv-nov-4.avif): Failed to open stream: No such file or directory in <b>main.php</b><br />
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//WordPress - MECv7.38.1//EN
X-WR-TIMEZONE:Asia/Jerusalem
"""
MIXER = r"""BEGIN:VEVENT
UID:MEC-b60001bc05562faa4557570c4afdec67@nbn.org.il
DTSTART;TZID=Asia/Jerusalem:20261206T194500
DTEND;TZID=Asia/Jerusalem:20261207T204500
SUMMARY:Singles Hanukkah Mixer
DESCRIPTION:\n\nForget the dating apps. This Hanukkah\, let’s meet in person!\nJoin The Right Time for a
  festive evening of mingling at the cozy Yom Tom Cafe in the heart of Tel Aviv!
URL:https://www.nbn.org.il/events/singles-hanukkah-mixer/
ORGANIZER;CN=The Right Time:MAILTO:
CATEGORIES:Chol HaMoed / Holidays,Social,Tel Aviv,Young Professionals
LOCATION:30 Yom Tov Street, Tel Aviv
ATTACH;FMTTYPE=image/jpeg:https://www.nbn.org.il/wp-content/uploads/2026/11/singles-chanukah-dec-6th.jpeg
END:VEVENT
"""
CHESS = r"""BEGIN:VEVENT
UID:MEC-df32ca880df44a866db5a3d505d3d125@nbn.org.il
DTSTART;TZID=Asia/Jerusalem:20261201T150000
DTEND;TZID=Asia/Jerusalem:20261201T163000
RRULE:FREQ=WEEKLY
SUMMARY:The Nighters Chess Club – Adults
DESCRIPTION:YOUR NEXT MOVE?\nChess with a Champion. A Game Worth Sharing.
URL:https://www.nbn.org.il/events/the-nighters-chess-club-adults/
ORGANIZER;CN=ESRA:MAILTO:bookingoffice@esra.org.il
CATEGORIES:40+,50+,Empty Nesters/Retirees,Herzliya,Social,Young Professionals
LOCATION:52 Yehuda HaNasi, Herzliya
ATTACH;FMTTYPE=image/png:https://www.nbn.org.il/wp-content/uploads/2026/10/The-Nighters-Chess-Club-Adults.png
END:VEVENT
"""
ZOOM = r"""BEGIN:VEVENT
UID:MEC-274e93c0b09bb80f1374fe3b4318e1bd@nbn.org.il
DTSTART;TZID=Asia/Jerusalem:20270103T200000
DTEND;TZID=Asia/Jerusalem:20270104T213000
RDATE;TZID=Asia/Jerusalem:20270103T200000,20270117T200000,20270207T200000
SUMMARY:Becoming a Nation
DESCRIPTION:An exclusive five-part online lecture series.
URL:https://www.nbn.org.il/events/becoming-a-nation/
CATEGORIES:40+,50+,Empty Nesters/Retirees,Lectures &amp; Workshops,Young Professionals,Zoom
LOCATION:Zoom
END:VEVENT
"""
WORKSHOP = r"""BEGIN:VEVENT
UID:MEC-774066fddd0e9587010394ab86e4f48d@nbn.org.il
DTSTART;TZID=Asia/Jerusalem:20261116T000000
DTEND;TZID=Asia/Jerusalem:20261117T000000
RDATE;VALUE=DATE:20261116,20261118,20261123,20261125
SUMMARY:Tablecloth Playhouse Workshop: A Hanukkah Gift for the Grandchildren (Talpiot)
DESCRIPTION:At Grannies’ Garage in Talpiot\, Allyson runs a two-day workshop.
URL:https://www.nbn.org.il/events/tablecloth-playhouse-workshop-a-hanukkah-gift-for-the-grandchildren-talpiot/
CATEGORIES:50+,Arts/Creativity,Jerusalem,Lectures &amp; Workshops
LOCATION:Poalei Tzedek 2, 3rd Floor, Talpiot, Jerusalem
END:VEVENT
"""
TOUR = r"""BEGIN:VEVENT
UID:MEC-5c0788ba808d4c4896101fdb28daf8c5@nbn.org.il
DTSTART;TZID=Asia/Jerusalem:20270121T200000
DTEND;TZID=Asia/Jerusalem:20270121T210000
SUMMARY:Eitan Levine vs. The Ayatollah A Comedy Tour!
URL:https://www.nbn.org.il/events/eitan-levine-vs-the-ayatollah-a-comedy-tour/
CATEGORIES:Film, Television &amp; Theater,Jerusalem,Modiin,Performance,Tel Aviv
END:VEVENT
"""
TAIL = "END:VCALENDAR\n"
PAGE = """<html><head><script type="application/ld+json">
{
"@context": "http://schema.org",
"@type": "Event",
"startDate": "2026-12-06T19:45:00+02:00",
"endDate": "2026-12-07",
"location": {"@type": "Place", "name": "Yom Tom Cafe", "image": "", "address": "30 Yom Tov Street, Tel Aviv"},
"organizer": {"@type": "Person", "name": "The Right Time", "url": ""},
"offers": {"url": "https://www.nbn.org.il/events/singles-hanukkah-mixer/", "price": "165", "priceCurrency": "NIS"},
"name": "Singles Hanukkah Mixer"
}
</script></head><body>
<div class="mec-event-cost"><div class="mec-cost">Cost</div><dl><dd class="mec-events-event-cost">NIS165.00</dd></dl></div>
<a class="mec-booking-button mec-bg-color" target="_blank" href="https://forms.gle/JPc62QhterPC7gGeA">Register Here</a>
</body></html>"""

CFG = NbnScraper.settings_model.model_validate(Settings().sources["nbn"])


def _parse(entry: str) -> list[ScrapedEvent]:
    (item,) = feed_events(HEAD + entry.replace("\n", "\r\n") + TAIL)
    return parse_vevent(item, CFG, TZ, NOW)


def test_mixer_from_the_feed_and_its_page() -> None:
    (mixer,) = _parse(MIXER)
    assert (mixer.source, mixer.source_event_id) == ("nbn", "b60001bc05562faa4557570c4afdec67")
    assert mixer.title == "Singles Hanukkah Mixer"
    # MEC's end is a day late: 19:45 to the next day's 20:45 ends the same evening.
    assert (mixer.start_at, mixer.end_at) == (
        datetime(2026, 12, 6, 19, 45, tzinfo=IL),
        datetime(2026, 12, 6, 20, 45, tzinfo=IL),
    )
    assert mixer.time_known
    assert (mixer.address, mixer.city, mixer.organizer) == ("30 Yom Tov Street, Tel Aviv", "תל אביב", "The Right Time")
    assert mixer.description and mixer.description.startswith("Forget the dating apps. This Hanukkah, let’s meet")
    assert mixer.images == ["https://www.nbn.org.il/wp-content/uploads/2026/11/singles-chanukah-dec-6th.jpeg"]
    assert mixer.source_genres == ["english", "holidays", "meetups"]  # the area category ("Tel Aviv") is no kind
    full = with_page(mixer, PAGE, CFG)
    assert (full.venue_name, full.address) == ("Yom Tom Cafe", "30 Yom Tov Street, Tel Aviv")
    assert (full.min_price, full.max_price, full.currency) == (Decimal(165), Decimal(165), "ILS")
    assert full.tickets[0].price == Decimal(165) and not full.tickets[0].is_free
    assert full.extra["purchase_url"] == "https://forms.gle/JPc62QhterPC7gGeA"
    enriched = Enricher(Settings().enrichment, TZ).enrich(full)
    assert enriched is not None and {"meetups", "english", "singles_dating", "holidays"} <= set(enriched.genres)


def test_weekly_club_gives_its_occurrences() -> None:
    games = _parse(CHESS)
    assert len(games) == 8  # Tuesdays from 1.12 to 60 days ahead (22.1)
    assert games[0].start_at == datetime(2026, 12, 1, 15, 0, tzinfo=IL)
    assert games[1].start_at == datetime(2026, 12, 8, 15, 0, tzinfo=IL)
    assert games[1].end_at == datetime(2026, 12, 8, 16, 30, tzinfo=IL)
    assert len({g.source_event_id for g in games}) == len(games)
    assert games[1].source_event_id == "df32ca880df44a866db5a3d505d3d125:202612081500"
    assert games[0].city == "הרצליה"
    assert games[0].source_genres == ["english", "meetups"]  # audiences (40+, retirees) declare nothing
    enriched = Enricher(Settings().enrichment, TZ).enrich(games[0])
    assert enriched is not None and "meetups" in enriched.genres


def test_online_and_placeless_events() -> None:
    assert _parse(ZOOM) == []  # "Zoom"
    tour = _parse(TOUR)  # no place in the feed: kept until its page says where (the run drops it without one)
    assert len(tour) == 1 and tour[0].address is None and tour[0].city is None  # three towns: none chosen
    assert tour[0].source_genres == ["english", "film", "theater", "shows"]


def test_all_day_dates() -> None:
    days = _parse(WORKSHOP)
    assert [d.start_at.date().isoformat() for d in days if d.start_at] == ["2026-11-23", "2026-11-25"]
    assert not days[0].time_known  # 00:00 to the next day's 00:00
    assert days[0].city == "ירושלים"


def test_run_drops_online_and_placeless_and_reads_each_page_once() -> None:
    # The feed's dates moved so the mixer is ten days from today: current whenever the test runs.
    shift = datetime.now(IL).date() + timedelta(days=10) - date(2026, 12, 6)

    def moved(entry: str) -> str:
        return re.sub(
            r"(?<![0-9])(\d{8})(?=T|$|,)",
            lambda m: (datetime.strptime(m.group(1), "%Y%m%d") + shift).strftime("%Y%m%d"),
            entry,
            flags=re.MULTILINE,
        )

    feed = HEAD + "".join(moved(e) for e in (MIXER, ZOOM, TOUR)) + TAIL
    scraper = build_scraper("nbn", Settings())
    assert isinstance(scraper, NbnScraper)
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.params.get("mec-ical-feed"):
            return httpx.Response(200, text=feed)
        if request.url.path == "/events/singles-hanukkah-mixer/":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(200, text="<html></html>")

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert not scraper.failures
    assert [e.title for e in events] == ["Singles Hanukkah Mixer"]
    assert events[0].venue_name == "Yom Tom Cafe"
    assert sorted(asked) == sorted(
        [
            "https://www.nbn.org.il/?mec-ical-feed=1",
            "https://www.nbn.org.il/events/eitan-levine-vs-the-ayatollah-a-comedy-tour/",
            "https://www.nbn.org.il/events/singles-hanukkah-mixer/",
        ]
    )
