"""Geocoder queries built from what sellers write in their address / venue fields."""

from types import SimpleNamespace

from partylinks.geo import candidates


def place(address: str | None = None, venue: str | None = None, city: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(address=address, venue_name=venue, city=city, in_target_city=False)


def test_ogo_parenthesised_venue_and_english_city() -> None:
    queries = candidates(place("(Rotch) Rothschild St 64, Rishon LeZion", city="ראשון לציון"))
    assert "Rothschild St 64, Rishon LeZion" in queries  # the bare English address: Nominatim's hit
    assert not any("(Rotch)" in q for q in queries)


def test_street_inside_the_venue_field() -> None:
    queries = candidates(place(venue="ויצו רחוב ראש פינה 12", city="רמת גן"))
    assert any(q.startswith("ראש פינה 12") for q in queries)


def test_country_alone_is_no_address() -> None:
    assert not any(q.startswith("Israel") for q in candidates(place("Israel")))


def test_a_street_named_after_another_town_is_no_contradiction() -> None:
    """"שוהם 73, חריש" is Shoham Street in Harish: OSM's answer there was refused as "not in Shoham" (Nov 2026)."""
    from partylinks.config import get_settings
    from partylinks.enrichment import Enricher
    from partylinks.geo import Geocoder

    cfg = get_settings()
    enricher = Enricher(cfg.enrichment, cfg.timezone)
    geo = Geocoder(cfg.geocoding, enricher.areas, cfg.enrichment.target_city.aliases, None, cfg.enrichment.cities)  # type: ignore[arg-type]
    harish = (32.4676477, 35.0455838)  # OSM: Shoham Street, Avnei Hen, Harish
    assert geo._bad(place("שוהם 73", "אולם תרבות", "חריש"), harish) is None
    assert geo._bad(place("(מתנ''ס חריש) Shoham St 73, Harish", "מתנ''ס חריש", "חריש"), harish) is None
    # A place word with no town of its own still decides: Kibbutz HaGoshrim is not in the Negev.
    assert geo._bad(place(None, "קיבוץ הגושרים"), (31.25, 34.79)) == "region"
    # The city field names the town before a town in the venue's street: Harish, not Shoham.
    assert enricher.canonical_city("חריש", None, "אולם תרבות חריש - כתובת: רח' שוהם 73 (מסומן)") == "חריש"


def test_an_address_names_its_town_last() -> None:
    """"כובשי אילת" is a street in Dimona: the town is the last part of the address (Nov 2026)."""
    from partylinks.config import get_settings
    from partylinks.enrichment import Enricher

    cfg = get_settings()
    enricher = Enricher(cfg.enrichment, cfg.timezone)
    assert enricher.canonical_city(None, "תיאטרון דימונה DimonaTheater, כובשי אילת, דימונה, ישראל") == "דימונה"
    assert enricher.canonical_city(None, "שדרות ירושלים 19, תל אביב") == "תל אביב"
    assert enricher.canonical_city(None, "רחוב הרצל 5, חולון, Israel") == "חולון"


def test_a_hall_or_address_naming_its_town_beats_the_sellers_city() -> None:
    """Eventim filed Afula's culture hall under Haifa, Eventer the Sherover theatre under Tel Aviv (Nov 2026)."""
    from partylinks.config import get_settings
    from partylinks.enrichment import Enricher

    cfg = get_settings()
    city = Enricher(cfg.enrichment, cfg.timezone).canonical_city
    assert city("חיפה", None, "היכל התרבות עפולה") == "עפולה"
    assert city("באר שבע", None, "תיאטראות אילת") == "אילת"
    assert city("תל אביב", None, "שרובר תיאטרון ירושלים") == "ירושלים"
    assert city("כפר סבא", 'UpTown Music Club - קרית עתידים, ת"א', "UpTown") == "תל אביב"
    # No town named whole: the city field stays.
    assert city("תל אביב", None, "תיאטרון גשר") == "תל אביב"
    assert city("גבעתיים", "שדה בוקר 10", "מרכז קהילתי שדה בוקר") == "גבעתיים"
    assert city("חריש", "רח' שוהם 73", "אולם תרבות חריש") == "חריש"
    # A village under its council keeps its name.
    assert city("נווה ירק", None, "היכל התרבות דרום השרון") == "נווה ירק"


def test_a_pinned_venue_name_decides_over_the_sellers_city() -> None:
    """The Khan theatre's shows at ZOA House (Tel Aviv) came with the Khan's city, Jerusalem; "גן לאומי מגדל צדק"
    (Rosh HaAyin) names the Galilee town Migdal. Both were refused as "region" though pinned by hand (Nov 2026)."""
    from partylinks.config import get_settings
    from partylinks.enrichment import Enricher
    from partylinks.geo import Geocoder

    cfg = get_settings()
    enricher = Enricher(cfg.enrichment, cfg.timezone)
    geo = Geocoder(cfg.geocoding, enricher.areas, cfg.enrichment.target_city.aliases, None, cfg.enrichment.cities)  # type: ignore[arg-type]
    for row in (place(None, "בית ציוני אמריקה", "ירושלים"), place(None, "גן לאומי מגדל צדק")):
        pin = geo._pin(row)
        assert pin is not None and geo._bad(row, pin) is None
    # A pin found only in the address still answers to the city field.
    assert geo._bad(place("קיבוץ הגושרים"), (31.25, 34.79)) == "region"
