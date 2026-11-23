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


def test_spellings_alike_but_for_punctuation_are_one_venue() -> None:
    def at(id: str, name: str, city: str) -> VenueInput:
        return VenueInput(id, name, name, None, None, None, city)

    rows = [
        at("a", "בית העם רחובות.", "רחובות"),
        at("b", "בית העם רחובות.", "רחובות"),
        at("c", "בית העם - רחובות", "רחובות"),
        at("d", "The Wave Club", "אילת"),
        at("e", "The wave club", "אילת"),
        at("f", "The Wave Club", "אילת"),
        at("g", "The wave club", "חיפה"),  # another city: another place
    ]
    assert infer_venues(rows) == {"c": "בית העם רחובות.", "e": "The Wave Club"}


def test_a_name_found_all_over_town_ties_no_halls_together() -> None:
    rows = [
        VenueInput("a", "מועדון א", "מועדון א", "הרצל 1", 32.05, 34.77, "תל אביב"),
        VenueInput("b", "קופה", "קופה", "הרצל 1", 32.05, 34.77, "תל אביב"),
        VenueInput("c", "קופה", "קופה", "דיזנגוף 9", 32.09, 34.77, "תל אביב"),
        VenueInput("d", "מועדון ב", "מועדון ב", "דיזנגוף 9", 32.09, 34.77, "תל אביב"),
    ]
    assert infer_venues(rows) == {}


def test_no_chain_from_one_building_to_the_next() -> None:
    """Prod, Nov 2026: Tzavta's dates showed as "תיאטרון הקאמרי". Ibn Gabirol 30 holds Tzavta, Gray and Bar Giyora;
    a form's placeholder name sat at that address and at Beit Ariela's, and one Cameri show at Beit Ariela's
    address chained the street keys into one venue."""
    tlv = "תל אביב"

    def at(id: str, name: str, address: str | None, lat: float | None = None, lng: float | None = None) -> VenueInput:
        return VenueInput(id, name, name, address, lat, lng, tlv)

    rows = [
        *[at(f"tz{i}", "צוותא", "שלמה אבן גבירול 30") for i in range(4)],
        at("tz-hall1", "צוותא 1", None, 32.0767, 34.7863),  # a hall, on Beit Ariela's pin
        at("tz-hall2", "צוותא 2, תל אביב", "אבן גבירול 30"),
        *[at(f"gray{i}", "מועדון GRAY", "שלמה אבן גבירול 30") for i in range(3)],
        *[at(f"bg{i}", "בר גיורא", "אבן גבירול 30") for i in range(3)],
        at("babu", "באבו בר", "אבן גבירול 30"),
        # a placeholder name at both buildings and elsewhere: it is in no one place
        at("form1", "הוספת ערך משלך ידנית", "אבן גבירול 30"),
        at("form2", "הוספת ערך משלך ידנית", "שאול המלך 25"),
        at("form3", "הוספת ערך משלך ידנית", "מרון חנה 2"),
        *[at(f"ariela{i}", "בית אריאלה", "שאול המלך 25") for i in range(3)],
        *[at(f"cameri{i}", "תיאטרון הקאמרי", "שאול המלך 19") for i in range(5)],
        at("cameri-away", "תיאטרון הקאמרי", "שאול המלך 25"),  # one show at another address
        at("cameri-hall", "תיאטרון הקאמרי - אולם 2", "שאול המלך 19"),
    ]
    assert infer_venues(rows) == {"tz-hall1": "צוותא", "tz-hall2": "צוותא", "cameri-hall": "תיאטרון הקאמרי"}


def test_spellings_at_one_place_still_merge() -> None:
    """One venue in two scripts and two spellings at one address is still one venue."""
    rows = [
        *[VenueInput(f"b{i}", "בארבי", "בארבי", "נמל יפו 1", None, None, "תל אביב") for i in range(3)],
        VenueInput("latin", "Barby", "Barby", "Jaffa Port 1, Tel Aviv", 32.0535, 34.7627, "תל אביב"),
        VenueInput("pin", "בארבי", "בארבי", "נמל יפו 1", 32.0535, 34.7627, "תל אביב"),
        VenueInput("club", "מועדון הבארבי", "מועדון הבארבי", "נמל יפו 1", None, None, "תל אביב"),
    ]
    assert infer_venues(rows) == {"latin": "בארבי", "club": "בארבי"}
