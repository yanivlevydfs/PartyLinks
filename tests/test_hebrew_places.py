from __future__ import annotations

from datetime import UTC, datetime

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.hebrew_places import HebrewPlaces, HebrewPlacesSettings
from partylinks.models import ScrapedEvent

SETTINGS = Settings()  # loads config/settings.yaml
PLACES = HebrewPlaces(
    HebrewPlacesSettings(
        names={
            "فندق رمادا": "מלון רמדה",
            "مطعم مندولين": "מסעדת מנדולין",
            "حيفا": "חיפה",
            "الاوديتوريم حيفا": "אודיטוריום חיפה",
            "المسرح العربي - أم الفحم": "התיאטרון הערבי - אום אל-פחם",
            "بيتح تكفا": "פתח תקווה",
        }
    )
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("فندق رمادا", "מלון רמדה"),
        ("الاوديتوريم حيفا", "אודיטוריום חיפה"),  # the longest name first, not "...  חיפה"
        ("المسرح العربي -  أم الفحم", "התיאטרון הערבי - אום אל-פחם"),  # spacing as the site wrote it
        ("فُنْدُق رمادا", "מלון רמדה"),  # diacritics
        ("(مطعم مندولين) כניסה ראשית, עיילבון", "(מסעדת מנדולין) כניסה ראשית, עיילבון"),
        ("Haifa حيفا חיפה", "Haifa חיפה"),  # the name it already says once
        ("Mazkeka מזקקה مزكيكا", "Mazkeka מזקקה"),  # an unlisted translation beside Hebrew: dropped
        ("(فندق الأمباسدور) Nablus Rd 5, Jerusalem", "Nablus Rd 5, Jerusalem"),
        ("مطعم ابو علي خويص, עומר בן אל-ח'טאב, ירושלים", "עומר בן אל-ח'טאב, ירושלים"),
        ("هاكارن، بيتح تكفا، Israel", "פתח תקווה, Israel"),  # the Arabic comma too
        ("مكان غير معروف", "مكان غير معروف"),  # wholly Arabic, unlisted: kept
        ("Rothschild Blvd 24, Tel Aviv", "Rothschild Blvd 24, Tel Aviv"),
        ("حيفاوي", "حيفاوي"),  # whole words only
        (None, None),
    ],
)
def test_hebrew_places(text: str | None, expected: str | None) -> None:
    assert PLACES(text) == expected


def test_keep_arabic_beside_other_when_off() -> None:
    places = HebrewPlaces(HebrewPlacesSettings(names={}, drop_beside_other=False))
    assert places("Mazkeka מזקקה مزكيكا") == "Mazkeka מזקקה مزكيكا"


def test_empty_hebrew_name_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        HebrewPlacesSettings(names={"فندق رمادا": " "})


def test_config_names_are_arabic_to_hebrew() -> None:
    names = SETTINGS.enrichment.hebrew_places.names
    assert names["فندق رمادا"] == "מלון רמדה"
    for arabic, hebrew in names.items():
        assert any("\u0600" <= ch <= "\u06ff" for ch in arabic), arabic
        assert not any("\u0600" <= ch <= "\u06ff" for ch in hebrew), hebrew
        assert any("\u0590" <= ch <= "\u05ff" for ch in hebrew), hebrew


def test_enrich_makes_tzkrti_venue_hebrew() -> None:
    enricher = Enricher(SETTINGS.enrichment, SETTINGS.timezone)
    event = ScrapedEvent(
        source="tzkrti",
        source_event_id="5245",
        url="https://tzkrti.com/en/checkout/5245",
        title="Christmas Market Ramada",
        start_at=datetime(2026, 12, 10, 16, 0, tzinfo=UTC),
        venue_name="فندق رمادا",
        address="Derekh ha-Tsiyonut 29, Nazareth, Israel",
        city="Nazareth",
    )
    enriched = enricher.enrich(event)
    assert enriched is not None
    assert enriched.venue_name == "מלון רמדה"
    assert enriched.venue_slug and not any("\u0600" <= ch <= "\u06ff" for ch in enriched.venue_slug)
    assert enriched.address == "Derekh ha-Tsiyonut 29, Nazareth, Israel"
    assert enriched.title == "Christmas Market Ramada"  # titles are never translated
