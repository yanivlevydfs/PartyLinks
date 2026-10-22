from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.scrapers.abroad import country_code, local_zone


def test_country_names_hebrew_english_codes_and_aliases() -> None:
    cases = {
        "גרמניה": "DE", "הולנד": "NL", "צ'כיה": "CZ", 'ארה"ב': "US", "בריטניה": "GB", "דובאי": "AE",
        "Spain": "ES", "the Netherlands": "NL", "UK": "GB", "deu": "DE", "fr": "FR", " Italy. ": "IT",
    }  # fmt: skip
    assert {name: country_code(name) for name in cases} == cases
    assert country_code("Atlantis") == country_code(None) == UNKNOWN_COUNTRY


def test_zone_by_city_then_country_then_default() -> None:
    assert local_zone("US", "Asia/Jerusalem", "Los Angeles") == "America/Los_Angeles"
    assert local_zone("GB", "Asia/Jerusalem", "London") == "Europe/London"  # not London, Ontario
    assert local_zone("ES", "Asia/Jerusalem", "מדריד") == "Europe/Madrid"  # unknown city: the country's
    assert local_zone(UNKNOWN_COUNTRY, "Asia/Jerusalem") == "Asia/Jerusalem"
