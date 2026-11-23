"""Airdrop: listing cards and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.airdrop import (
    AirdropScraper,
    AirdropSettings,
    at_club,
    club_key,
    club_profile,
    item_list_urls,
    listing_urls,
    pager_urls,
    parse_event,
    parse_page,
    sitemap_urls,
)
from partylinks.venue_profiles import VENUE_PROFILE

TZ = "Asia/Jerusalem"
SITE = "https://airdrop.co.il"
FIXTURES = Path(__file__).parent / "fixtures/airdrop"


def _event(name: str, path: str):
    return parse_event((FIXTURES / name).read_text("utf-8"), f"{SITE}/{path}", TZ)


def test_listing_links_every_card_once() -> None:
    urls = listing_urls((FIXTURES / "events.html").read_text("utf-8"), SITE)
    assert len(urls) >= 40 and len(urls) == len(set(urls))
    assert urls[0] == f"{SITE}/justloveit-fullmoon"
    assert f"{SITE}/shalvata/spoons-last-dance" in urls


def test_listing_cards_in_another_markup() -> None:
    page = (
        '<a href="/wtgl-marina" class="event-card x"><h3>WTGL</h3></a>'
        "<a class='ev-card' href='/goat/backstage-records-tuesday'>B</a>"
        '<a class="nav" href="/clubs">Clubs</a><a class="card" href="#top">up</a>'
    )
    assert listing_urls(page, SITE) == [f"{SITE}/wtgl-marina", f"{SITE}/goat/backstage-records-tuesday"]


def _pager(*pages: int) -> str:
    """The site's pager: page 1 is the plain listing URL, the others ``&amp;page=N``."""
    hrefs = ["/events?when=upcoming" + (f"&amp;page={p}" if p > 1 else "") for p in pages]
    links = "".join(f'<a class="pagination__link" href="{h}">p</a>' for h in hrefs)
    return f'<nav class="pagination">{links}<span class="pagination__link pagination__link--current">x</span></nav>'


def test_pager_links_unescaped_once_each() -> None:
    assert pager_urls(_pager(1, 3, 3), SITE) == [
        f"{SITE}/events?when=upcoming",
        f"{SITE}/events?when=upcoming&page=3",
    ]


def test_listing_reads_every_page_to_the_last() -> None:
    # 2026-11-23: "97 אירועים" on 3 pages (48 + 48 + 1); only page 1 used to be read.
    listing = f"{SITE}/events?when=upcoming"
    pages = {
        listing: '<a class="wcard glass" href="/a"></a><a class="wcard" href="/b"></a>' + _pager(2),
        f"{listing}&page=2": '<a class="wcard" href="/c"></a>' + _pager(1, 3),
        f"{listing}&page=3": '<a class="wcard" href="/d"></a>' + _pager(2, 4),
        f"{listing}&page=4": '<a class="wcard" href="/d"></a>' + _pager(3, 5),  # past the last: repeats it
    }
    read: list[str] = []

    async def get_text(url: str, **_: object) -> str:
        read.append(url)
        return pages[url]

    settings = AirdropSettings(cron="25 6,18 * * *", site_url=SITE, listing_path="/events?when=upcoming")
    scraper = AirdropScraper(settings, HttpSettings(), TZ)
    scraper.get_text = get_text  # type: ignore[method-assign]
    assert asyncio.run(scraper._listing()) == [f"{SITE}/{p}" for p in "abcd"]
    assert read == [listing, f"{listing}&page=2", f"{listing}&page=3", f"{listing}&page=4"]


def test_club_night_with_price_range_and_seller_link() -> None:
    e = _event("shalvata_spoons-last-dance.html", "shalvata/spoons-last-dance")
    assert e is not None and e.source_event_id == "shalvata/spoons-last-dance"
    assert e.title == "Spoons בשלוותה · סגירת עונת הקיץ"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-26T22:00:00+02:00"
    assert e.venue_name == "שלוותה תל אביב" and e.city == "תל אביב" and e.country is None
    assert (e.min_price, e.max_price, e.currency) == (Decimal(110), Decimal(125), "ILS")
    assert e.artists == ["DARCO", "ADAM TEN"] and e.artist_profiles[0].image_url
    assert e.source_genres == ["טכנו ואלקטרוני"]
    assert e.extra["purchase_url"] == "https://www.eventer.co.il/event/dyk2f/LxL9L"


def test_referral_dropped_and_end_before_start_rolls_over() -> None:
    e = _event("justloveit-fullmoon.html", "justloveit-fullmoon")
    assert e is not None and e.min_price == Decimal("134.1")
    assert e.extra["purchase_url"] == "https://go-out.co/event/1791731808221"
    assert e.end_at is not None and e.start_at is not None and e.end_at > e.start_at


def test_trip_abroad_gets_its_country() -> None:
    e = _event("anyma-athens.html", "anyma-athens")
    assert e is not None and e.country == "GR" and e.city == "אתונה" and e.venue_name == "OAKA"
    assert e.extra["purchase_url"] == "https://takebreaktravel.com/events/anyma-athens-31-10"
    # The page stamps Israel's +02:00 on Athens' wall time; read it in Athens' zone.
    assert e.start_at is not None and e.start_at.hour == 16 and str(e.start_at.tzinfo) == "Europe/Athens"


def test_artist_page_gives_profile_and_event_links_not_an_event() -> None:
    page = (
        '<script type="application/ld+json">{"@context": "https://schema.org", "@type": "MusicArtist",'
        ' "name": "ARTBAT", "url": "https://airdrop.co.il/artbat", "image": "https://airdrop.co.il/a.jpg",'
        ' "subjectOf": [{"@type": "MusicEvent", "name": "ZAMNA", "url": "https://airdrop.co.il/zamna-montreal",'
        ' "startDate": "2026-11-21T19:00:00+02:00"}]}</script>'
    )
    event, profile, links = parse_page(page, f"{SITE}/artbat", TZ)
    assert event is None
    assert profile is not None and (profile.name, profile.image_url) == ("ARTBAT", "https://airdrop.co.il/a.jpg")
    assert links == [f"{SITE}/zamna-montreal"]


def test_sitemap_pages_without_home() -> None:
    xml = f"<urlset><url><loc>{SITE}/</loc></url><url><loc>{SITE}/utopia</loc></url><url><loc>{SITE}/utopia</loc></url></urlset>"
    assert sitemap_urls(xml, SITE) == [f"{SITE}/utopia"]


def test_hebrew_page_has_one_spelling_in_listing_sitemap_and_json_ld() -> None:
    # 2026-11-23: the listing's href was percent-encoded, the sitemap's <loc> Hebrew: 4 parties stored twice.
    encoded = f"{SITE}/%D7%90%D7%A8%D7%98-%D7%A7%D7%9C%D7%90%D7%91"
    xml = f"<urlset><url><loc>{SITE}/ארט-קלאב</loc></url><url><loc>{SITE}/ארט-קלאב/</loc></url></urlset>"
    assert sitemap_urls(xml, SITE) == [encoded]
    assert listing_urls(f'<a class="wcard" href="{encoded[len(SITE) :]}"></a>', SITE) == [encoded]
    page = (
        '<script type="application/ld+json">{"@context": "https://schema.org", "@type": "ItemList",'
        ' "itemListElement": [{"@type": "ListItem", "url": "/ארט-קלאב#top"}]}</script>'
    )
    assert item_list_urls(page, SITE) == [encoded]


def test_clubs_list_names_every_club_page() -> None:
    urls = item_list_urls((FIXTURES / "clubs.html").read_text("utf-8"), SITE)
    assert len(urls) == 16 and urls[1] == f"{SITE}/shalvata" and f"{SITE}/echo-tlv" in urls


def test_artists_list_names_artist_pages() -> None:
    urls = item_list_urls((FIXTURES / "artists.html").read_text("utf-8"), SITE)
    assert len(urls) >= 40 and urls[0] == f"{SITE}/adam-ten" and len(urls) == len(set(urls))


def test_club_page_gives_venue_profile() -> None:
    club = club_profile((FIXTURES / "shalvata.html").read_text("utf-8"), f"{SITE}/shalvata")
    assert club is not None and club.key == "airdrop-shalvata" and club.name == "שלוותה תל אביב"
    assert club.address == "התערוכה 3, תל אביב"
    assert club.latitude is not None and club.longitude is not None
    assert round(club.latitude, 3) == 32.101 and round(club.longitude, 3) == 34.775
    assert club.about and club.logo_url and club.logo_url.startswith(f"{SITE}/uploads/")
    assert "https://www.instagram.com/shalvatatelaviv/" in club.links
    # An event or artist page is no club.
    assert club_profile((FIXTURES / "adam-ten.html").read_text("utf-8"), f"{SITE}/adam-ten") is None


def test_hebrew_club_slug_gets_an_ascii_key() -> None:
    key = club_key(f"{SITE}/%D7%94%D7%91%D7%95%D7%A1%D7%AA%D7%9F")
    assert key.startswith("airdrop-") and key.isascii() and "%" not in key
    assert key == club_key(f"{SITE}/הבוסתן")


def test_club_night_gets_club_pin_address_and_profile() -> None:
    club = club_profile((FIXTURES / "shalvata.html").read_text("utf-8"), f"{SITE}/shalvata")
    assert club is not None
    e = _event("shalvata_spoons-last-dance.html", "shalvata/spoons-last-dance")
    assert e is not None and e.latitude is None
    placed = at_club(e, {club.key: club})
    assert (placed.latitude, placed.longitude) == (club.latitude, club.longitude)
    assert placed.address == "התערוכה 3, תל אביב" and placed.extra[VENUE_PROFILE] == "airdrop-shalvata"
    assert placed.extra["purchase_url"] == e.extra["purchase_url"]
    # A festival elsewhere stays as it is.
    trip = _event("anyma-athens.html", "anyma-athens")
    assert trip is not None and at_club(trip, {club.key: club}) is trip


def test_artist_page_profile_with_spotify_links_and_genre() -> None:
    _, profile, _ = parse_page((FIXTURES / "adam-ten.html").read_text("utf-8"), f"{SITE}/adam-ten", TZ)
    assert profile is not None and profile.name == "ADAM TEN" and profile.image_url
    assert profile.spotify_url and "open.spotify.com/artist/" in profile.spotify_url
    assert "https://www.instagram.com/adamten_/" in profile.links and profile.spotify_url not in profile.links
    assert profile.genre == "טכנו ואלקטרוני"


def test_club_night_gets_site_genres() -> None:
    e = _event("shalvata_spoons-last-dance.html", "shalvata/spoons-last-dance")
    assert e is not None
    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(e)
    assert enriched is not None and enriched.genres
