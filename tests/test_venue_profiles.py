import io

from PIL import Image

from partylinks.venue_profiles import VenueProfile, match, to_webp
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
