import io
import os

from PIL import Image

from partylinks.db import Database, dialect_insert
from partylinks.venue_profiles import VenueBook, VenueProfile, cached_profiles, match, to_webp
from partylinks.venue_profiles import VenueProfileRow as Row
from partylinks.venue_profiles.eventer import parse_venue

MOON = VenueProfile(key="eventer-moon", name="מון צ׳יילד | Moon Child", source="eventer",
                    aliases=["מון צ׳יילד", "Moon Child"], latitude=32.0971, longitude=34.7729, logo_url="x")  # fmt: skip
PARK = VenueProfile(key="eventer-park", name="הפארק | Park", source="eventer", aliases=["הפארק", "Park"],
                    latitude=32.08, longitude=34.78)  # fmt: skip


def test_names_are_tied_by_the_sites_link_or_the_whole_name() -> None:
    found = match([MOON, PARK], {
        "Moonchild - מון צ'ילד": None,  # the site's own events name this profile
        "מון צ'יילד": None,  # the whole name, spelled with or without a geresh
        "Moon Child": (31.25, 34.79),  # the same name in Beersheba: another place
        "מון": (32.5, 35.0),  # a shared word is not the venue
        "פארק הירקון": (32.10, 34.81),  # one name inside another is not the venue
        "הפארק": None,
        "Unknown": None,
    }, linked={"Moonchild - מון צ'ילד": "eventer-moon", "Unknown": "eventer-gone"})  # fmt: skip
    assert {n: p.key for n, p in found.items()} == {
        "Moonchild - מון צ'ילד": "eventer-moon", "מון צ'יילד": "eventer-moon", "הפארק": "eventer-park",
    }  # fmt: skip


def test_match_is_the_same_with_its_lookup_kept_for_the_list() -> None:
    profiles = [MOON, PARK]
    venues: dict[str, tuple[float, float] | None] = {"מון צ'יילד": None, "הפארק": None, "Unknown": None}
    first = match(profiles, venues)
    assert match(profiles, venues) == first  # the second call reads the kept lookup
    assert {n: p.key for n, p in match([PARK], venues).items()} == {"הפארק": "eventer-park"}  # another list: rebuilt


def test_cached_profiles_are_kept_per_database_until_a_save() -> None:
    db = Database(os.environ["PARTYLINKS_DATABASE__URL"])
    db.create_schema()
    book = VenueBook(db, quality=78)
    book.save([PARK])
    with db.session(write=False) as session:
        first = cached_profiles(session)
    assert [p.key for p in first] == ["eventer-park"]
    with db.session() as session:  # another process saves a profile: this one sees it once the cache expires
        session.execute(dialect_insert(session, Row).values(key="eventer-x", name="X", source="eventer"))
    with db.session(write=False) as session:
        assert cached_profiles(session) is first
        assert {p.key for p in cached_profiles(session, max_age=0)} == {"eventer-park", "eventer-x"}
    book.save([MOON])  # this process's own save drops the cache at once
    with db.session(write=False) as session:
        assert {p.key for p in cached_profiles(session)} == {"eventer-park", "eventer-x", "eventer-moon"}
    other = Database(os.environ["PARTYLINKS_DATABASE__URL"])  # another engine: its own entry
    with other.session(write=False) as session:
        assert cached_profiles(session) is not first


def test_logo_becomes_a_smaller_webp() -> None:
    png = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 128)).save(png, "PNG")
    webp = to_webp(png.getvalue(), 78)
    assert webp is not None and webp[8:12] == b"WEBP"
    assert Image.open(io.BytesIO(webp)).size == (960, 480)
    assert to_webp(b"not a picture", 78) is None


def test_eventer_venue_page() -> None:
    p = parse_venue(
        {"linkName": "moon_child", "title": "x", "images": {"imageSquare": "sq.jpg"}},
        {"_id": "63f0", "name": "מון צ׳יילד | Moon Child", "desc": " מועדון בנמל ", "locationDescription": "נמל תל אביב",
         "location": {"latitude": 32.09, "longitude": "34.77"}, "images": {"imageDefault": "wide.jpg"},
         "instagramLink": "https://instagram.com/moon", "facebookLink": "", "webSiteLink": "https://bio.link/moon"},
        "https://www.eventer.co.il/venues/moon_child",
    )  # fmt: skip
    assert p.key == "eventer-63f0" and p.aliases == ["מון צ׳יילד", "Moon Child"]
    assert (p.about, p.address, p.latitude, p.longitude) == ("מועדון בנמל", "נמל תל אביב", 32.09, 34.77)
    assert p.links == ["https://bio.link/moon", "https://instagram.com/moon"] and p.logo_url == "wide.jpg"
