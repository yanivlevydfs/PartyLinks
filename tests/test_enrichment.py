from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.artistdb import ArtistBook
from partylinks.config import Settings
from partylinks.db import Database
from partylinks.enrichment import Enricher
from partylinks.models import EntryType, ScrapedEvent, TicketTier

SETTINGS = Settings()  # loads config/settings.yaml
ENRICHER = Enricher(SETTINGS.enrichment, SETTINGS.timezone)

DESCRIPTION = """אחת המסיבות הכי חמות בעיר.

🎧 LINE UP

MAIN STAGE – MAINSTREAM & EDM:

DOR HADAD
MIA B2B MIKA
w/ SEAN ALONI

📅 חמישי | 22.10
⏰ 22:30

הפתעות כל הלילה עם DJ REYES על הבר.
"""


def make(**overrides: Any) -> ScrapedEvent:
    base: dict[str, Any] = {
        "source": "test",
        "source_event_id": "1",
        "url": "https://example.com/e/1",
        "title": "Techno Thursday",
        "description": DESCRIPTION,
        # 23:30 Thursday in Tel Aviv
        "start_at": datetime(2026, 10, 22, 20, 30, tzinfo=UTC),
        "address": "Rothschild Blvd 24, Tel Aviv-Yafo, Israel",
    }
    return ScrapedEvent(**(base | overrides))


def test_lineup_extracted_from_description() -> None:
    event = ENRICHER.enrich(make())
    assert event is not None
    assert event.lineup == ["DOR HADAD", "MIA", "MIKA", "SEAN ALONI", "DJ REYES"]


def test_promo_bullets_are_not_a_lineup() -> None:
    promo = "🎶 ליינאפ מטורף\n🌿 לוקיישן קסום\n🌀 סאונד ללא פשרות…\n\n🎧 הליינאפ\n\nברוח היום במה כולה ישראלית 🇮🇱\n"
    event = ENRICHER.enrich(make(description=promo))
    assert event is not None and event.lineup == []


def test_lineup_names_are_cleaned_and_split() -> None:
    text = (
        "LINE UP\nAZVELOS - Birthday Set\nrotemx {Q}\nLANDAU B2B ANSTY\nYAARA RONמיינסטרים עד הבוקר\n"
        "DRESS CODE: SHREK\n\nTBA LINE UP\n\nHIP HOP\nTATTOO ZONE\nALON VAINER\n\nKIPI\n\n"
        "ינגנו DJ Orel Amos ו-PLECEL, לפי DJ Mag. DJ\xa0PERLMAN\nAFROPIANO\n"
    )
    event = ENRICHER.enrich(make(description=text))
    assert event is not None
    assert event.lineup == ["AZVELOS", "rotemx", "LANDAU", "ANSTY", "YAARA RON", "ALON VAINER", "KIPI",
                            "DJ Orel Amos", "DJ PERLMAN"]  # fmt: skip


def test_glued_names_split_on_known_artists() -> None:
    enricher = Enricher(SETTINGS.enrichment, SETTINGS.timezone)
    enricher.learn_artists(["MILLERO", "DALI", "Jackie", "MISSTAKEN", "MOSKOMILLEROADARU", "DJ SHAY"])
    text = "LINE UP -\nMOSKOMILLEROADARU\nDALIASKDODI\nJACKIEDERISIUS\nDJ JUDASZMISSTAKENAND MORE TBA\nGOLDSCHLAGER\n"
    event = enricher.enrich(make(description=text))
    assert event is not None
    assert event.lineup == ["MOSKO", "MILLERO", "ADARU", "DALI", "ASKDODI", "JACKIE", "DERISIUS",
                            "DJ JUDASZ", "MISSTAKEN", "GOLDSCHLAGER"]  # fmt: skip


def test_artist_book_learns_and_blocks(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'a.db'}")
    db.create_schema()
    book = ArtistBook(db)
    book.record(["MILLERO", "MILLERO", "Shalos"])
    book.set("OPEN AIR", blocked=True)
    known, blocked = book.load()
    assert known == {"millero", "shalos"} and blocked == {"open air"}
    enricher = Enricher(SETTINGS.enrichment, SETTINGS.timezone)
    enricher.learn_artists(known, blocked)
    event = enricher.enrich(make(description="LINE UP\nMOSKOMILLEROADARU\nOPEN AIR\n"))
    assert event is not None and event.lineup == ["MOSKO", "MILLERO", "ADARU"]


def test_featured_artists_spotted_in_text() -> None:
    enricher = Enricher(SETTINGS.enrichment, SETTINGS.timezone)
    enricher.learn_artists([], featured=["Adam Ten", "Dali", "Melodic", "ko", "עדן בן זקן", "חיים"])
    text = "ערב melodic techno עם ADAM TEN ו-DALI, והפתעה: עדן בן זקן. dali אינו שם; Kobe. חיים טובים"
    event = enricher.enrich(make(title="Kosmos", description=text))
    assert event is not None and event.lineup == ["Adam Ten", "Dali", "עדן בן זקן"]


def test_time_categories_in_local_timezone() -> None:
    event = ENRICHER.enrich(make())
    assert event is not None
    assert (str(event.local_date), event.day_of_week, event.time_slot, event.is_weekend) == (
        "2026-10-22", "thursday", "night", True,
    )  # fmt: skip


def test_genres_from_text_and_declared_types() -> None:
    event = ENRICHER.enrich(make(event_types=["house"]))
    assert event is not None
    assert {"techno", "house", "mainstream", "edm"} <= set(event.genres)


def test_film_text_is_not_read_for_music_genres() -> None:
    drive_in = make(title="קולנוע דרייב אין | 29.10", description="פסקול של ג'אז, יוונית ומוזיקה אלקטרונית")
    event = ENRICHER.enrich(drive_in)
    assert event is not None and event.genres == ["cinema"]
    film = ENRICHER.enrich(make(title="האהבות שלנו", description="", source_genres=["קולנוע"]))
    assert film is not None and film.genres == ["cinema"]


def test_entry_type_free_and_paid() -> None:
    tiers = [TicketTier(name="כניסה חינם עד 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(80))]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    assert event.entry_type is EntryType.FREE_AND_PAID and event.has_free_entry


def test_sold_out_free_tier_is_not_free_entry() -> None:
    tiers = [
        TicketTier(name="להרשמה חינם עד השעה 00:00", price=Decimal(0), is_sold_out=True),
        TicketTier(name="Entry Ticket", price=Decimal(99)),
    ]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    assert event.entry_type is EntryType.PAID and not event.has_free_entry and event.free_sold_out


def test_sold_out_event_is_not_free_entry() -> None:
    tiers = [TicketTier(name="כניסה חינם", price=Decimal(0), is_sold_out=True)]
    event = ENRICHER.enrich(make(tickets=tiers, is_sold_out=True))
    assert event is not None and not event.has_free_entry


def test_entry_type_from_price_range_without_tiers() -> None:
    event = ENRICHER.enrich(make(min_price=Decimal(100), max_price=Decimal(120)))
    assert event is not None and event.entry_type is EntryType.PAID


def test_target_city_matching() -> None:
    assert ENRICHER.enrich(make()).in_target_city is True  # type: ignore[union-attr]
    haifa = ENRICHER.enrich(make(address="Haifa, Israel"))  # every city is kept, flagged outside
    assert haifa is not None and haifa.in_target_city is False and haifa.area == "haifa"
    tlv_only = SETTINGS.enrichment.model_copy(update={"target_city": SETTINGS.enrichment.target_city.model_copy(update={"drop_other_cities": True})})
    assert Enricher(tlv_only, SETTINGS.timezone).enrich(make(address="Haifa, Israel")) is None


def test_event_abroad_gets_a_country() -> None:
    """Go-Out sells nights in Rome / Athens: coordinates abroad move them out of the Israeli feed."""
    rome = ENRICHER.enrich(make(address="Via Roma 1, Roma, Italy", latitude=41.9, longitude=12.5))
    assert rome is not None and rome.area == "abroad" and rome.country == "ZZ"
    haifa = ENRICHER.enrich(make(address="Haifa, Israel"))
    assert haifa is not None and haifa.country is None


def test_slugs_are_readable_unique_and_keep_hebrew() -> None:
    a = ENRICHER.enrich(make(title="מסיבת טכנו | Thursday!", venue_name="Moonchild - מון צ'ילד"))
    b = ENRICHER.enrich(make(source_event_id="2", title="מסיבת טכנו | Thursday!"))
    assert a is not None and b is not None
    assert a.slug is not None and a.slug.startswith("מסיבת-טכנו-thursday-2026-10-22-test-")
    assert a.slug != b.slug
    assert a.venue_slug == "moonchild-מון-צ-ילד"


def test_zero_priced_pay_at_door_tier_is_paid_with_price_from_name() -> None:
    tiers = [
        TicketTier(name="\u200fכרטיס מזומן 120", price=Decimal(0)),
        TicketTier(name="רשומים חינם עד 00:30", price=Decimal(0)),
    ]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    cash, free = event.tickets
    assert cash.pay_at_door and cash.price == Decimal(120) and cash.kind.value == "paid"
    assert free.kind.value == "free" and free.price == 0
    assert event.entry_type is EntryType.FREE_AND_PAID and event.max_price == Decimal(120)


def test_free_at_the_door_stays_free() -> None:
    event = ENRICHER.enrich(make(tickets=[TicketTier(name="חינם בכניסה עד 00:00", price=Decimal(0))]))
    assert event is not None and event.tickets[0].kind.value == "free" and not event.tickets[0].pay_at_door


def test_zero_priced_code_redemption_is_not_free_entry() -> None:
    tiers = [
        TicketTier(name="קוד למימוש מועדונים - כרטיס כניסה למוזיאון", price=Decimal(0), is_free=True),
        TicketTier(name="מבוגר", price=Decimal(40)),
    ]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None and not event.has_free_entry and event.entry_type is EntryType.PAID
    assert event.min_price == Decimal(40)


def test_discount_from_original_price_or_coupon_text() -> None:
    plain = ENRICHER.enrich(make(tickets=[TicketTier(name="רגיל", price=Decimal(80))]))
    cheaper = ENRICHER.enrich(make(tickets=[TicketTier(name="מוקדם", price=Decimal(60), original_price=Decimal(80))]))
    coded = ENRICHER.enrich(make(description="לפרטים נוספים וקוד הנחה שלחו לנו הודעה"))
    assert plain is not None and not plain.has_discount
    assert cheaper is not None and cheaper.has_discount
    assert coded is not None and coded.has_discount


def test_area_by_keyword_then_coordinates() -> None:
    areas = Enricher(SETTINGS.enrichment, SETTINGS.timezone).areas
    # Keyword in the address wins.
    assert areas.match("בארבי", "קיבוץ גלויות 52, תל אביב-יפו", None, None, True) == "south"
    assert areas.match("Gray Club", "אבן גבירול 30", None, None, True) == "center"
    assert areas.match("Cappella", "Cappella, הארבעה, Tel Aviv-Yafo", 32.0698, 34.786, True) == "business"
    # "Tel Aviv-Yafo" in an address must not mean Jaffa.
    assert areas.match("Humble Club", "Rothschild Blvd 19, Tel Aviv-Yafo", 32.0633, 34.7742, True) == "rothschild"
    # No keyword: nearest center within its radius.
    assert areas.match("Moonchild", "Moonchild, Tel Aviv-Yafo", 32.1002, 34.7745, True) == "north"
    assert areas.match("Somewhere", None, 31.0, 35.0, True) is None
    # Outside the target city: the region, by place name (city first), then by coordinates.
    assert areas.match("Lobby Night Club", "מקלף 1, חיפה", 32.789, 34.98, False) == "haifa"
    assert areas.match("Bar", "אחוזה 120", None, None, False, "רעננה") == "sharon"
    assert areas.match("Bar", "Main St", 31.25, 34.79, False) == "negev"
    assert areas.match("Club", "Somewhere", 52.37, 4.9, False) == "abroad"
    assert areas.match("Club", None, None, None, False, "Amsterdam") == "abroad"
    assert areas.match("Club", None, None, None, False) == "outside"


def test_hebrew_city_names_match_whole_words_only() -> None:
    def city(**fields: object) -> bool | None:
        event = ENRICHER.enrich(make(**({"address": None} | fields)))
        return False if event is None else event.in_target_city  # None = dropped as another city

    assert city(city="ראשון לציון", venue_name="פטיפון בר ראשון לציון") is False  # "יפו" inside "פטיפון"
    assert city(venue_name="מסיבה ביפו העתיקה") is True  # attached prefix letter
    assert city(address="שדרות רוטשילד 24, תל אביב-יפו") is True
    assert city(venue_name="הודנא בר, ת\"א") is True


def test_hebrew_genre_words_match_whole_words_only() -> None:
    lecture = make(title="קפה דעת", description="האתגרים החברתיים והטכנולוגיים של ישראל")
    assert "techno" not in ENRICHER._detect_genres(lecture)  # "טכנו" inside "טכנולוגיים"
    party = make(title="FRIDAY", description="והטכנו הכי טוב בעיר")
    assert "techno" in ENRICHER._detect_genres(party)  # attached prefixes still count


def test_ballroom_dancing_from_its_own_words_only() -> None:
    def ballroom(title: str, description: str = "") -> bool:
        return "ballroom" in ENRICHER._detect_genres(make(title=title, description=description))

    assert ballroom("ערב ריקודים סלוניים בדיזנגוף")
    assert ballroom("מסיבת ריקודי סלון", "וואלס, טנגו ופוקסטרוט")
    assert ballroom("Tango del Sur", "Argentine tango night")
    assert not ballroom("James Kennedy At Clarendon Ballroom")  # a venue
    # A Strauss waltz and a Piazzolla tango in a concert are not a dance night.
    assert not ballroom("הפילהרמונית: שטראוס", "וואלסים מווינה וטנגו של פיאצולה")


def test_adult_nightlife_genres_from_strong_phrases_only() -> None:
    adult = {"liberal", "kink", "sensual", "gender_balance"}

    def found(title: str, description: str) -> set[str]:
        return adult & set(ENRICHER._detect_genres(make(title=title, description=description)))

    line = "אהבה ליברליות 👑\nבעלי ליין הבדסמ המאנצ'-פליי\nמקדמים את הקהילה הליברלית בישראל\nחילופי זוגות, בדסמ"
    assert found("מסיבה בחמישי", line) == {"liberal", "kink"}
    assert found("SINNERS & SAINTS FETISH PLAY PARTY", "קוד לבוש לטקס ועור") == {"kink"}
    assert found("ערב טנטרה לזוגות", "") == {"sensual"}
    assert found("Friday Night", "הכניסה באיזון מגדרי, זוגות ורווקות") == {"gender_balance"}
    # A hammer, Edvard Munch, liberal politics and a "sensual" salsa show are none of them.
    assert found("נגרות לילדים", "מביאים פטיש ומסמרים") == set()
    assert found("The Scream: Edvard Munch", "") == set()
    assert found("הרצאה", "הציונות הליברלית ועתידה") == set()
    assert found("לילות הוואנה", "מופע חושני של סלסה, תלבושות לטקס") == set()
    # Seen on prod: a stand-up topic, a play's politics and a dance review.
    assert found("אלה קנבסקי | סטנדאפ", "הורים גרושים, פוליאמוריה, לגדול בבית רוסי") == set()
    assert found("בית ברנרדה אלבה", "נחשב לדמות מסוכנת בשל דעותיו הליברליות") == set()
    assert found("להקת PHILDANCO", "טכניקה איתנה כסלע ואקרובטיקה ארוטית") == set()
    assert found("INSANE 🪩 מסיבת נוסטלגיה ליברלית!", "") == {"liberal"}


def test_talk_takes_no_genres_from_its_description() -> None:
    talk = make(title="הרצאה - ריקוד המכונה", description="כך נולדו ההיפ הופ, ההאוס והטכנו")
    assert ENRICHER._detect_genres(talk) == []


def test_named_region_overrides_far_coordinates() -> None:
    areas = ENRICHER.areas
    assert areas.contradicts(None, "פילוסוף קיבוץ הגושרים", None, 30.99, 35.35)  # placed in the Negev
    assert not areas.contradicts(None, "פילוסוף קיבוץ הגושרים", None, 33.22, 35.62)


def test_tech_event_text_gives_tech_genres_not_music() -> None:
    from partylinks.config import get_settings
    from partylinks.enrichment import Enricher
    from partylinks.models import ScrapedEvent

    settings = get_settings()
    enricher = Enricher(settings.enrichment, settings.timezone)

    def genres(title: str, description: str, declared: list[str]) -> list[str]:
        event = ScrapedEvent(source="x", source_event_id="1", url="https://example.com", title=title,
                             description=description, event_types=declared)  # fmt: skip
        return enricher._detect_genres(event)

    meetup = genres("Founders night", "Israeli startup founders talk GenAI and kubernetes", ["Technology"])
    assert meetup == ["ai", "cloud_devops", "hi_tech", "startups"]  # not "israeli"
    assert genres("AI night", "", ["AI"]) == ["ai"]  # "ai" trusted only when declared
    assert "ai" not in genres("Rave", "AI generated visuals all night, techno", [])
    assert genres("מסיבה ישראלית", "להיטים ישראליים", []) == ["israeli", "mainstream"]


def test_words_in_passing_are_no_genres() -> None:
    def genres(title: str, description: str, declared: tuple[str, ...] = ()) -> list[str]:
        return ENRICHER._detect_genres(make(title=title, description=description, source_genres=list(declared)))

    # Real listings (October 2026) that were tagged by a word in passing.
    smurfs = genres("הדרדסים ההצגה", "הדמויות שכבשו את מסכי הטלוויזיה והקולנוע\nיזמים ומפיקים ראשיים: דוריס", ("הצגות ילדים",))
    assert smurfs == ["kids", "theatre"]  # its declared category; not cinema, not startups
    assert genres("נישואים גרעיניים", "קומדיה זוגית. יש להציג את הכרטיס האלקטרוני בכניסה") == []
    assert genres("מנגינה של סתיו", "התמונה נוצרה בבינה מלאכותית.") == []
    assert genres("Cold Brew workshop", "🇮🇱 עברית\nיואב, איש דאטה ביום") == []  # language, a day job
    assert genres("Classico screening", "המשחק: דור רומנו, סול כהן רוזנברג") == []  # Sol is a name
    assert genres("Pilots party", "כל שבוע, למעט ערבי אלכוהול חופשי") == []  # "ערבי": evenings, not Arabic
    # Tech from the title still counts; the description only for declared tech events.
    assert "ai" in genres("בינה מלאכותית | היכרות עם מודל שפה", "")
    assert "startups" in genres("Founders night", "startup founders meet", ("hi-tech",))
    assert "electronic" in genres("Skazi", "מסיבה אלקטרונית עד הבוקר")
    assert "stand_up" in genres("Comedy night", "", ("קומדיה",))  # comedy declared by the source


def test_end_before_start_is_the_next_day_or_unknown() -> None:
    start = datetime(2026, 10, 29, 20, 30, tzinfo=UTC)
    assert make(start_at=start, end_at=datetime(2026, 10, 29, 1, 0, tzinfo=UTC)).end_at == datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
    assert make(start_at=start, end_at=start).end_at == start.replace(day=30)  # Zygo: same clock time
    assert make(start_at=start, end_at=datetime(2026, 10, 20, tzinfo=UTC)).end_at is None  # days before: unknown


def test_verbs_and_pop_ups_are_no_genres() -> None:
    assert "conference" not in ENRICHER._detect_genres(make(title="BAMESHEK", description="אפשר להכנס עד 23:00"))
    assert "pop" not in ENRICHER._detect_genres(make(title="Avenue Matcha Pop-Up & Party", description=""))
