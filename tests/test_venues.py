from partylinks.venues import VenueInput, infer_venues, street_key


def row(id: str, name: str | None, address: str | None, lat: float | None = None, lng: float | None = None, stored: str | None = "same") -> VenueInput:
    return VenueInput(id, name if stored == "same" else stored, name, address, lat, lng)


def test_infer_venues() -> None:
    rows = [
        # spellings of one club at one spot merge under the most common real name
        row("jimmy1", "JIMMYWHO? Bar & Lounge", "x", 32.0629419, 34.7719619),
        row("jimmy2", "JIMMYWHO? Bar & Lounge", "y", 32.0629419, 34.7719619),
        row("jimmy3", "Jimmy who tel aviv", "z", 32.0629419, 34.7719619),
        # an address-like name never beats a real one, even with more votes
        *[row(f"addr{i}", "שדרות רוטשילד 24", "שדרות רוטשילד 24", 32.0629419, 34.7719619) for i in range(3)],
        # same street + number merges too (no coordinates needed)
        row("barby1", "בארבי", "קיבוץ גלויות 52, תל אביב-יפו", 32.0535, 34.7627),
        row("barby2", "בארבי", "קיבוץ גלויות 52"),
        row("barby3", "מועדון הבארבי", "דרך קיבוץ גלויות 52"),
        row("gray", "Gray Club", "אבן גבירול 30 קומה 1-"),
        # a site's default pin, shared by unrelated streets: coordinates ignored
        row("pin-a", "Somewhere", "Herzl 1", 32.5, 34.5),
        row("pin-b", None, "Allenby 5", 32.5, 34.5),
        row("pin-c", None, "Dizengoff 9", 32.5, 34.5),
        # nameless events take the venue at their spot / street, else their street address
        row("goout", None, "Rothschild Blvd 24, Tel Aviv-Yafo, Israel", 32.06294, 34.77196),
        row("selector", None, "אבן גבירול 30 תל אביב"),
        row("street", None, "HaArba'a St 8, Tel Aviv-Yafo, 6473908, Israel", 32.0, 34.0),
        row("city", None, "Tel Aviv-Yafo, Israel", 32.0853, 34.7818),
        row("pobox", None, "Post box 6371 Hangar 23, Tel Aviv port, Israel"),
        # a stale stored name is recomputed from what the site said
        row("stale", None, "Rothschild Blvd 24", 32.06294, 34.77196, stored="Old"),
    ]
    jimmy = "JIMMYWHO? Bar & Lounge"
    assert infer_venues(rows) == {
        "jimmy3": jimmy,
        **{f"addr{i}": jimmy for i in range(3)},
        "barby3": "בארבי",
        "pin-b": "Allenby 5",
        "pin-c": "Dizengoff 9",
        "goout": jimmy,
        "selector": "Gray Club",
        "street": "HaArba'a St 8",
        "stale": jimmy,
    }


def test_street_key_ignores_street_words() -> None:
    assert street_key("Rothschild Blvd 24, Tel Aviv") == street_key("rothschild 24")
    assert street_key("Tel Aviv-Yafo, Israel") is None


def test_one_name_in_two_cities_is_two_venues() -> None:
    def at(id: str, name: str | None, address: str, city: str | None) -> VenueInput:
        return VenueInput(id, name, name, address, None, None, city)

    rows = [
        # A producer's name on its touring shows: the Cameri in Tel Aviv, and a show in Be'er Sheva.
        at("tlv1", "הקאמרי", "שאול המלך 19", "תל אביב"),
        at("tlv2", "תיאטרון הקאמרי", "שאול המלך 19", "תל אביב"),
        at("tlv3", "הקאמרי", "שאול המלך 19", "תל אביב"),
        at("bs1", "תיאטרון באר שבע", "שדרות יצחק רגר 41", "באר שבע"),
        at("bs2", "הקאמרי", "שדרות יצחק רגר 41", "באר שבע"),
        at("bs3", "תיאטרון באר שבע", "שדרות יצחק רגר 41", "באר שבע"),
        # same street + number in another town is another place
        at("rishon", "היכל התרבות", "הרצל 10", "ראשון לציון"),
        at("holon", "בית העם", "הרצל 10", "חולון"),
        # no city: the venue's city when its name is known in one city only
        at("nocity", "תיאטרון באר שבע", "", None),
    ]
    assert infer_venues(rows) == {"tlv2": "הקאמרי", "bs2": "תיאטרון באר שבע"}
