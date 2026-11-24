"""Ozen Bar: Tickera ``tc_events`` posts from wp-json."""

from __future__ import annotations

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ozen import OzenSettings, parse_post, purchase_link

CFG = OzenSettings(cron="0 0 * * *", latitude=32.0743905, longitude=34.7758641)

POST = {
    "id": 174274,
    "status": "publish",
    "link": "https://ozentelaviv.com/event/%d7%9e%d7%a1%d7%99%d7%91%d7%aa-mtv/",
    "title": {"rendered": "מסיבת MTV &#8211; 📺✨"},
    "content": {
        "rendered": '<p>לרכישת כרטיסים &gt;&gt; <a href="https://go-out.co/event/1794145433370" target="_blank">'
        "https://go-out.co/event/1794145433370</a></p><p>מסיבת MTV חוזרת!</p>"
        '<p><a href="https://www.instagram.com/ozentlv">ozen</a></p>'
    },
    "meta": {"event_date_time": "2026-11-27 22:00", "event_end_date_time": "2026-11-28 04:00"},
    "_embedded": {
        "wp:featuredmedia": [{"source_url": "https://ozentelaviv.com/wp-content/uploads/2026/11/mtv.png"}],
        "wp:term": [
            [{"taxonomy": "event_category", "name": "מסיבות"}],
            [{"taxonomy": "artist", "name": "ערן זרחוביץ"}],
        ],
    },
}


def test_post() -> None:
    event = parse_post(POST, CFG, "Asia/Jerusalem")
    assert event is not None
    assert event.source_event_id == "174274"
    assert event.title == "מסיבת MTV – 📺✨"
    assert event.start_at is not None and event.end_at is not None
    assert event.start_at.isoformat() == "2026-11-27T22:00:00+02:00"
    assert event.end_at.isoformat() == "2026-11-28T04:00:00+02:00"
    assert (event.venue_name, event.city) == ("אוזן בר", "תל אביב")
    assert (event.latitude, event.longitude) == (32.0743905, 34.7758641)
    assert event.source_genres == ["מסיבות"]
    assert event.lineup == ["ערן זרחוביץ"]
    assert [str(i) for i in event.images] == ["https://ozentelaviv.com/wp-content/uploads/2026/11/mtv.png"]
    assert event.extra["purchase_url"] == "https://go-out.co/event/1794145433370"
    settings = Settings()
    assert "nightclubs" in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)


def test_end_before_start_pdf_poster_and_no_seller() -> None:
    post = POST | {
        "meta": {"event_date_time": "2026-12-06 20:00", "event_end_date_time": "2026-12-06 15:31"},
        "content": {"rendered": '<p><a href="https://www.instagram.com/ozentlv">ig</a></p>'},
        "_embedded": {"wp:featuredmedia": [{"source_url": "https://ozentelaviv.com/wp-content/uploads/a.pdf"}]},
    }
    event = parse_post(post, CFG, "Asia/Jerusalem")
    assert event is not None
    assert event.end_at is None
    assert event.images == []
    assert "purchase_url" not in event.extra
    assert event.source_genres == []


def test_skipped_posts_and_seller_hosts() -> None:
    assert parse_post(POST | {"status": "draft"}, CFG, "Asia/Jerusalem") is None
    assert parse_post(POST | {"meta": {"event_date_time": ""}}, CFG, "Asia/Jerusalem") is None
    html = '<a href="https://www.tickettailor.com/events/gilliaptercomedy/2470466">x</a>'
    assert purchase_link(html, CFG.ticket_hosts) == "https://www.tickettailor.com/events/gilliaptercomedy/2470466"
