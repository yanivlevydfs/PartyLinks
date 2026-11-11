"""Smart search: the words people type ("טכנו בתל אביב עד 100", "free jazz berlin this weekend") become
filters. Known names (cities, regions, countries, genres) match their events and the text; price and
date words become price / date filters; every other word must appear in the event's text fields.
Searches every event, in Israel and abroad."""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from difflib import get_close_matches
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from convertdate import hebrew, holidays
from sqlalchemy import ColumnElement, SQLColumnExpression, String, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import aliased

from partylinks import countries, seo
from partylinks.config import Settings
from partylinks.db import EventRow, lineup_contains, whole_word
from partylinks.places import city_abroad

Cond = ColumnElement[bool]
_listing = aliased(EventRow)  # another listing of the same event (its group), for ticket-site names
HE_PREFIXES = ("", "ב", "ל", "מ", "ה", "ו", "וב")  # "בתל אביב", "לחיפה", "מברלין"
FREE = {"free", "חינם", "בחינם", "כניסה חופשית", "כניסה חינם", "ללא עלות"}
ABROAD = {"abroad", "חול", "חו״ל", 'חו"ל', "בחול", "בחו״ל", 'בחו"ל'}
PRICE = re.compile(r'(?:עד|under|below|max|<)\s*(\d{2,4})|(\d{2,4})\s*(?:₪|ש"ח|ש״ח|שח|nis|ils)', re.IGNORECASE)
DAYS = {
    "tonight": 0, "today": 0, "הערב": 0, "היום": 0, "הלילה": 0,
    "tomorrow": 1, "מחר": 1, "מחר בערב": 1,
}  # fmt: skip
WEEKEND = {
    "weekend",
    "this weekend",
    "סופש",
    "סופ״ש",
    'סופ"ש',
    "בסופש",
    "בסופ״ש",
    'בסופ"ש',
    "סוף שבוע",
    "בסוף השבוע",
    "סוף השבוע",
}

# Chat words that are not what an event's text holds ("בא לי", "find me"): left in, each would have to match and
# find nothing. Dropped from the free words only, after known names and dates are taken.
FILLER = {
    "מה", "יש", "לי", "לנו", "תמצא", "תמצאי", "תחפש", "תחפשי", "תביא", "תביאי", "תראה", "תראי", "תן", "תני",
    "אני", "אנחנו", "רוצה", "רוצים", "מחפש", "מחפשת", "מחפשים", "בא", "באלי", "משהו", "איזה", "איזשהו",
    "אירוע", "אירועים", "דברים", "של", "את", "גם", "אולי", "בבקשה", "פליז", "טוב", "טובים", "טובה", "כל", "הכל",
    "איפה", "מתי", "לצאת", "לבלות", "בילוי", "בילויים", "שווה", "שווים", "מומלץ", "מומלצים", "כיף", "כיפי", "נחמד",
    "אפשר", "צריך", "יודע", "תגיד", "תגידי", "ומה", "עם", "קורה", "הולך", "ללכת", "נלך", "לאן",
    "please", "pls", "what", "whats", "what's", "is", "are", "there", "any", "anything", "some", "something",
    "find", "me", "show", "i", "want", "wanna", "looking", "for", "events", "event", "things", "to", "do", "go",
    "on", "the", "a", "an", "at", "good", "best", "fun", "happening", "can", "you", "get", "us", "we", "and",
    "about", "how", "in", "of", "with", "this", "near",
    # "ביום שישי הקרוב", "מסיבה עד 100 שקל", "מה יש לעשות": words around a date, price or wish
    "הקרוב", "הקרובה", "הקרובים", "הקרובות", "הזה", "הזו", "הזאת", "coming", "upcoming", "next",
    "שקל", "שקלים", "לעשות", "לראות", "לשמוע", "שאפשר", "מעניין", "מעניינים", "פעילות", "פעילויות", "activity", "activities",
}  # fmt: skip

HOLIDAY_NAMES = {
    "hanukkah": ("חנוכה", "hanukkah", "chanukah", "hanukah"), "purim": ("פורים", "purim"),
    "passover": ("פסח", "passover", "pesach"), "shavuot": ("שבועות", "shavuot"),
    "rosh_hashanah": ("ראש השנה", "rosh hashanah"), "sukkot": ("סוכות", "sukkot"),
    "tu_bishvat": ("ט״ו בשבט", 'ט"ו בשבט', "טו בשבט", "tu bishvat"),
    "lag_baomer": ("ל״ג בעומר", 'ל"ג בעומר', "לג בעומר", "lag baomer"),
    "tu_bav": ("ט״ו באב", 'ט"ו באב', "טו באב", "tu bav"), "independence_day": ("יום העצמאות", "עצמאות", "independence day"),
    "halloween": ("הלואין", "האלווין", "הלווין", "halloween"), "new_year": ("סילבסטר", "new year", "new years eve"),
    "valentines": ("ולנטיין", "יום האהבה", "valentine", "valentines"),
}  # fmt: skip
# Same dates every year: (month, day) from, to; parties run in the days before.
YEARLY_DATES = {"halloween": ((10, 25), (10, 31)), "new_year": ((12, 28), (1, 1)), "valentines": ((2, 10), (2, 14))}
# Bare "מאי", "יוני", "יולי" are names too: only "במאי", "ביוני", "חודש יולי".
MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6, "יולי": 7, "אוגוסט": 8,
    "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}  # fmt: skip
BARE_MONTHS = {"מאי", "יוני", "יולי"}
# "march" and "may" are words: only "in march", "in may".
EN_MONTHS = {
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12, "in march": 3, "in may": 5,
}  # fmt: skip
# Python's weekday() (Monday 0). A bare "ראשון" is also ראשון לציון: only "יום ראשון" / "ביום ראשון".
WEEKDAYS = {"ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5}
EN_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _next_month(today: date, month: int) -> tuple[date, date]:
    start = date(today.year + (month < today.month), month, 1)
    return max(start, today), _month_end(start)


def _next_weekday(today: date, weekday: int) -> tuple[date, date]:
    d = today + timedelta(days=(weekday - today.weekday()) % 7)
    return d, d


def _holiday(year: int, key: str) -> tuple[date, date]:
    """A holiday in Israel in a Gregorian year, eve to last day."""
    if key in YEARLY_DATES:
        (m1, d1), (m2, d2) = YEARLY_DATES[key]
        return date(year, m1, d1), date(year + ((m2, d2) < (m1, d1)), m2, d2)
    day = timedelta(days=1)
    if key == "tu_bav":
        d = date(*hebrew.to_gregorian(year + 3760, hebrew.AV, 15))
        return d - day, d
    if key == "independence_day":  # 5 Iyar, moved off Friday / Saturday / Monday
        d = date(*hebrew.to_gregorian(year + 3760, hebrew.IYYAR, 5))
        d += {4: -day, 5: -2 * day, 0: day}.get(d.weekday(), 0 * day)
        return d - day, d
    d = date(*getattr(holidays, {"tu_bishvat": "tu_beshvat"}.get(key, key))(year))
    last = {"hanukkah": 7, "passover": 6, "sukkot": 7, "rosh_hashanah": 1, "purim": 1}.get(key, 0)
    first = 4 if key == "purim" else 0 if key == "tu_bishvat" else 1  # Purim parties run the days before
    return d - first * day, d + last * day


def _next_holiday(today: date, key: str) -> tuple[date, date]:
    for year in (today.year - 1, today.year, today.year + 1):
        start, end = _holiday(year, key)
        if end >= today:
            return max(start, today), end
    raise AssertionError(key)


# A day by number, Israeli order (day first): "7.11", "ב-7/11", "7.11.26", "07/11/2026".
EXACT_DATE = re.compile(r"(?<!\S)(?:[בלמ]-?)?(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?!\S)")


def exact_dates(text: str, today: date) -> dict[str, date]:
    """Each "7.11"-style phrase in ``text`` -> its date; no year: the next one (a date passed this year is next year's)."""
    out = {}
    for m in EXACT_DATE.finditer(text):
        day, month, year = int(m[1]), int(m[2]), m[3]
        y = (2000 + int(year) if len(year) == 2 else int(year)) if year else today.year
        try:
            d = date(y, month, day)
            if not year and d < today:
                d = date(y + 1, month, day)
        except ValueError:
            continue  # not a date ("40.5", "31.2"): left as a word
        out[m[0]] = d
    return out


@lru_cache(maxsize=4)
def date_ranges(today: date) -> dict[str, tuple[date, date]]:
    """Search phrase -> the dates it means from ``today``: "השבוע", "בדצמבר", "ביום שישי", "חנוכה"."""
    week = (today, today + timedelta(days=6))
    sunday = today + timedelta(days=(6 - today.weekday()) % 7 or 7)
    next_week = (sunday, sunday + timedelta(days=6))
    month = (today, _month_end(today))
    after = _month_end(today) + timedelta(days=1)
    next_month = (after, _month_end(after))
    out = {
        "מחרתיים": (today + timedelta(days=2), today + timedelta(days=2)),
        "השבוע": week, "this week": week, "שבוע הבא": next_week, "בשבוע הבא": next_week, "next week": next_week,
        "החודש": month, "this month": month, "חודש הבא": next_month, "בחודש הבא": next_month, "next month": next_month,
    }  # fmt: skip
    for name, m in MONTHS.items():
        for phrase in ("ב" + name, "חודש " + name, *(() if name in BARE_MONTHS else (name,))):
            out[phrase] = _next_month(today, m)
    for name, m in EN_MONTHS.items():
        out[name] = _next_month(today, m)
    for name, weekday in WEEKDAYS.items():
        for phrase in ("יום " + name, "ביום " + name, *(("ב" + name,) if weekday in (3, 4, 5) else ())):
            out[phrase] = _next_weekday(today, weekday)
    for name, weekday in EN_WEEKDAYS.items():
        out[name] = _next_weekday(today, weekday)
    for key, names in HOLIDAY_NAMES.items():
        with warnings.catch_warnings():  # convertdate 2.5.1 calls its own deprecated month_days()
            warnings.filterwarnings("ignore", "month_days is deprecated", DeprecationWarning)
            span = _next_holiday(today, key)
        for name in names:
            out[name] = span
            if re.match(r"[֐-׿]", name):
                out["ב" + name] = span
    return out


# Words people search by that name a kind of event rather than a genre label.
PARTY = ("techno", "house", "trance", "mainstream", "hip_hop", "electronic", "nightclubs", "reggaeton_latin", "afro",
         "disco_funk", "drum_and_bass", "bass_dubstep", "hardstyle", "edm", "throwback", "mizrahi", "rnb", "dancehall_reggae", "dark_wave")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "מסיבה": PARTY, "מסיבות": PARTY, "מסיבת": PARTY, "party": PARTY, "parties": PARTY, "ליין": PARTY, "רייב": PARTY, "rave": PARTY,
    "מסיבת טבע": ("nature_party", "trance"), "מסיבות טבע": ("nature_party", "trance"), "פסיכדלי": ("psytrance",), "psytrance": ("psytrance",), "פסי טראנס": ("psytrance",), "פסיי": ("psytrance",), "psy": ("psytrance",),
    "קיי פופ": ("kpop",), "kpop": ("kpop",), "חסידי": ("hasidic",), "חסידית": ("hasidic",), "חזנות": ("jewish_music",), "מלודיק": ("melodic_techno",), "melodic": ("melodic_techno",),
    "הופעה": ("live_show",), "הופעות": ("live_show",), "הופעה חיה": ("live_show",), "קונצרט": ("live_show", "classical_opera"),
    "concert": ("live_show",), "live": ("live_show",), "מועדון": ("nightclubs",), "מועדונים": ("nightclubs",), "קלאב": ("nightclubs",),
    "הצגה": ("theatre",), "הצגות": ("theatre",), "תאטרון": ("theatre",), "מחזמר": ("theatre",), "סרט": ("cinema",), "סרטים": ("cinema",),
    "ילדים": ("kids",), "משפחה": ("kids",), "הרצאה": ("lectures",), "סדנה": ("workshops",), "סדנאות": ("workshops",),
    "פסטיבל": ("festival",), "פסטיבלים": ("festival",), "קומדיה": ("stand_up",), "stand up": ("stand_up",), "סטנד אפ": ("stand_up",),
    "ג'אז": ("jazz",), "היפהופ": ("hip_hop",), "hiphop": ("hip_hop",), "ראפ": ("hip_hop",), "rap": ("hip_hop",),
    "גאה": ("lgbt",), "להטב": ("lgbt",), "להט״ב": ("lgbt",), "חדר בריחה": ("escape_rooms",), "חדרי בריחה": ("escape_rooms",),
    "בריכה": ("pools",), "בריכות": ("pools",), "פארק מים": ("pools",), "פארקי מים": ("pools",),
    "מסיבת בריכה": ("pool_party",), "מסיבות בריכה": ("pool_party",), "pool party": ("pool_party",),
    "אלכוהול חופשי": ("open_bar",), "open bar": ("open_bar",), "40 פלוס": ("over_40",), "40+": ("over_40",), "נוער": ("youth",),
    "הפי אוור": ("happy_hour",), "יוגה": ("yoga_wellness",), "אופרה": ("classical_opera",), "קלאסית": ("classical_opera",),
    "רוק": ("rock_indie",), "אינדי": ("rock_indie",), "דיסקו": ("disco_funk",), "רגאטון": ("reggaeton_latin",), "לטיני": ("reggaeton_latin",),
}  # fmt: skip
# Every kind of event by the words people use for it (singular, plural, slang, English), on top of the
# category labels themselves (_genre_names): a search names a category in many ways. Only words that name
# the category itself: a sub-kind ("מרתון", "יין", "מטאל") is matched as a word, not as its whole category.
SYNONYMS.update({
    "קולנוע": ("cinema",), "הקרנה": ("cinema",), "הקרנות": ("cinema",), "הקרנת": ("cinema",), "בכורה": ("cinema",),
    "movie": ("cinema",), "movies": ("cinema",), "film": ("cinema",), "films": ("cinema",), "screening": ("cinema",),
    "תיאטרון": ("theatre",), "מחזה": ("theatre",), "מחזות": ("theatre",), "מחזות זמר": ("theatre",), "הצגת": ("theatre",),
    "play": ("theatre",), "plays": ("theatre",), "theater": ("theatre",), "musical": ("theatre",), "musicals": ("theatre",),
    "סטנדאפ": ("stand_up",), "סטנד-אפ": ("stand_up",), "סטנדאפיסט": ("stand_up",), "קומי": ("stand_up",),
    "comedy": ("stand_up",), "standup": ("stand_up",), "stand-up": ("stand_up",), "קומדיות": ("stand_up",),
    "הרצאות": ("lectures",), "שיחה": ("lectures",), "lecture": ("lectures",), "lectures": ("lectures",), "talk": ("lectures",), "talks": ("lectures",),
    "סדנת": ("workshops",), "קורס": ("workshops",), "קורסים": ("workshops",), "workshop": ("workshops",), "workshops": ("workshops",),
    "תערוכה": ("exhibitions",), "תערוכות": ("exhibitions",), "גלריה": ("exhibitions",), "גלריות": ("exhibitions",), "מוזיאון": ("exhibitions",),
    "מוזיאונים": ("exhibitions",), "אמנות": ("exhibitions",), "exhibition": ("exhibitions",), "museum": ("exhibitions",),
    "museums": ("exhibitions",), "gallery": ("exhibitions",), "art": ("exhibitions",),
    "לילדים": ("kids",), "משפחות": ("kids",), "לכל המשפחה": ("kids",), "פעוטות": ("kids",), "תינוקות": ("kids",),
    "kids": ("kids",), "children": ("kids",), "family": ("kids",), "families": ("kids",),
    "סיור": ("tours",), "סיורים": ("tours",), "טיול": ("tours",), "טיולים": ("tours",), "סיור מודרך": ("tours",),
    "tour": ("tours",), "tours": ("tours",), "trip": ("tours",), "hike": ("tours",),
    "ספורט": ("sport",),
    "sport": ("sport",), "sports": ("sport",),
    "טעימות": ("food",), "טעימת": ("food",), "קולינריה": ("food",),
    "food": ("food",), "tasting": ("food",),
    "מסעדה": ("fine_dining",), "ארוחת שף": ("fine_dining",), "dinner": ("fine_dining",),
    "שוק": ("markets",), "יריד": ("markets",), "ירידים": ("markets",), "פשפשים": ("markets",), "market": ("markets",), "fair": ("markets",),
    "ריקוד": ("dance",), "ריקודים": ("dance",), "בלט": ("dance",), "dance": ("dance",), "ballet": ("dance",),
    "בצ'אטה": ("latin_dance",), "בצאטה": ("latin_dance",), "bachata": ("latin_dance",), "קיזומבה": ("latin_dance",), "kizomba": ("latin_dance",),
    "salsa": ("salsa",), "ריקודים סלוניים": ("ballroom",), "סלוני": ("ballroom",),
    "מדיטציה": ("yoga_wellness",), "רוחניות": ("yoga_wellness",), "ריטריט": ("yoga_wellness",), "yoga": ("yoga_wellness",),
    "meditation": ("yoga_wellness",), "wellness": ("yoga_wellness",), "retreat": ("yoga_wellness",),
    "festival": ("festival",), "festivals": ("festival",), "קונצרטים": ("live_show", "classical_opera"), "concerts": ("live_show",), "gig": ("live_show",),
    "קלאסי": ("classical_opera",), "מוזיקה קלאסית": ("classical_opera",), "תזמורת": ("classical_opera",), "פילהרמונית": ("classical_opera",),
    "classical": ("classical_opera",), "opera": ("classical_opera",), "orchestra": ("classical_opera",), "symphony": ("classical_opera",),
    "מזרחי": ("mizrahi",), "קריוקי": ("karaoke",), "karaoke": ("karaoke",), "כנס": ("conference",), "conference": ("conference",),
    "מפגש": ("meetups",), "meetup": ("meetups",), "meetups": ("meetups",), "גיימינג": ("gaming",), "gaming": ("gaming",), "esports": ("gaming",),
    "התנדבות": ("volunteering",), "volunteering": ("volunteering",), "אטרקציה": ("attractions",), "attractions": ("attractions",),
    "האפי האוור": ("happy_hour",), "happy hour": ("happy_hour",), "גייז": ("lgbt",), "גיי": ("lgbt",), "קוויר": ("lgbt",), "גאווה": ("lgbt",),
    "gay": ("lgbt",), "queer": ("lgbt",), "lgbt": ("lgbt",), "pride": ("lgbt",), "teen": ("youth",), "teens": ("youth",),
    "ארבעים פלוס": ("over_40",), "פופ": ("pop",), "pop": ("pop",), "rock": ("rock_indie",), "indie": ("rock_indie",),
    "היפ הופ": ("hip_hop",), "hip hop": ("hip_hop",), "trap": ("hip_hop",), "טראפ": ("hip_hop",), "אפרוביטס": ("afro",), "afrobeats": ("afro",),
    "אלקטרונית": ("electronic",), "אלקטרוני": ("electronic",), "electronic": ("electronic",), "electro": ("electronic",),
    "רטרו": ("throwback",), "retro": ("throwback",), "שנות ה-90": ("nineties",), "שנות ה90": ("nineties",), "90s": ("nineties",), "ניינטיז": ("nineties",),
    "שנות ה-80": ("eighties",), "שנות ה80": ("eighties",), "80s": ("eighties",), "אייטיז": ("eighties",),
    "כשר": ("kosher",), "כשרה": ("kosher",), "כשרים": ("kosher",), "kosher": ("kosher",), "קפה": ("coffee_carts",), "עגלת קפה": ("coffee_carts",), "הייטק": ("hi_tech",), "hitech": ("hi_tech",), "סטארטאפ": ("startups",), "startup": ("startups",),
})  # fmt: skip
REGION_ALIASES = {
    "gush_dan": ("גוש דן", "מרכז", "המרכז", "center"), "haifa": ("קריות", "הקריות", "krayot"),
    "galilee": ("צפון", "הצפון", "גליל", "הגליל", "north"), "negev": ("דרום", "הדרום", "נגב", "הנגב", "south"),
}  # fmt: skip
SOURCE_ALIASES = {
    "goout": ("go out", "go-out", "גו אאוט", "גואאוט"), "eventer": ("אוונטר", "איוונטר"), "zygo": ("זייגו",),
    "tickchak": ("טיקצ'אק", "טיקצאק"), "goshow": ("גו שואו", "גושואו", "go show"), "tzkrti": ("תזכרתי", "tazkarti", "تذكرتي"), "fever": ("פיבר", "candlelight", "קנדללייט"), "towerofdavid": ("מגדל דוד", "tower of david", "החיזיון הלילי"), "tamuseum": ("מוזיאון תל אביב", "tel aviv museum of art"), "eventpro": ("מרכז קהילתי עמק חפר", "מתנס דרום נתניה", "ספריות נתניה", "sifrianet", "eventpro"), "swaptix": ("סוופטיקס", "סווטיקס", "swap tix"), "jaffatheatre": ("תיאטרון יפו", "jaffa theatre", "סראיא"), "smarticket": ("סמרטיקט",), "smarticket_il": ("פריינדס", "friends"), "barby": ("בארבי",), "reading3": ("רידינג", "רידינג 3", "רדינג", "רדינג 3", "reading 3", "reading"), "kupatbravo": ("קופת ברבו", "kupat bravo"), "easytix": ("איזיטיקס", "easy tix"),
    "ticketmaster": ("טיקטמאסטר",), "mevalim": ("מבלים",), "hulyo": ("הוליו",), "popit": ("פופיט",), "beatradar": ("beat radar", "ביט ראדר"), "iwant": ("i want", "איוונט", "איי וונט"), "music80s": ("80smusic", "80s music", "80 מיוזיק"),
    "happytlv": ("happy tlv", "הפי תל אביב"), "secrettelaviv": ("secret tel aviv",), "secrettelavivapp": ("secret tel aviv app",), "klika": ("klika", "קליקה"), "gototlv": ("gototlv", "daniel tel aviv", "דניאל תל אביב"), "mugics": ("mugics", "מיוזיקס", "מוגיקס"), "efokulam": ("efokulam", "efo kulam", "אפו כולם", "איפה כולם"), "liveisrael": ("liveisrael", "live israel", "לייב ישראל"), "plusparty": ("plus party", "plusparty", "מסיבות פלוס", "פלוס פארטי"), "cinemacity": ("cinema city", "סינמה סיטי"),
    "hotcinema": ("hot cinema", "הוט סינמה"), "ravhen": ("rav hen", "רב חן"), "lev": ("סינמה לב",), "zappa": ("זאפה",),
    "zoatlv": ("zoa tlv", "zoa house"), "eventimlight": ("eventim light", "eventim-light"),
    "selector": ("סלקטור",), "grayclub": ("gray club", "גריי קלאב"), "chillz": ("צ'ילז", "צילז"),
    "megatickets": ("mega tickets", "מגה טיקטס"), "eventbuzz": ("event buzz", "איוונט באז"), "epgb": ("radio epgb",),
    "tiktik": ("tik tik", "טיק טיק", "טיקטיק"), "gagarin": ("גגארין",), "babebar": ("babe bar", "בייב בר"), "comy": ("קומי",),
    "eventim": ("אוונטים", "איוונטים"), "jerusalemtheatre": ("jerusalem theatre", "תיאטרון ירושלים"),
    "agol": ("עגול", "אגול"), "airdrop": ("air drop", "אירדרופ", "אייר דרופ"), "vibez": ("וייבז",), "shotgun": ("שוטגאן",),
    "eventbrite": ("איוונטברייט",), "ticketingo": ("טיקטינגו", "ticketingo", "sportsevents365"), "showshappening": ("shows happening", "שואוז הפנינג"), "luma": ("לומה",),
    "geektime": ("גיקטיים",), "peoplecomputers": ("people and computers", "אנשים ומחשבים"), "parks": ("רשות הטבע והגנים", "רט\"ג", "גנים לאומיים", "שמורות טבע", "parks authority"), "meetup": ("מיטאפ",),
    "beershevatheatre": ("תיאטרון באר שבע",), "nikonitai": ("ניקו ניתאי", "niko nitai"), "planet": ("yes planet", "יס פלאנט"),
    "cinematheque_tlv": ("סינמטק תל אביב", "tel aviv cinematheque"), "cinematheque_jlm": ("סינמטק ירושלים", "jerusalem cinematheque"),
    "movieland": ("מובילנד",), "kishreyteufa": ("קשרי תעופה",), "ticketplace": ("ticket place", "טיקט פלייס"),
    "livevents": ("live events", "לייב איוונטס"), "issta": ("איסתא",), "megaevents": ("mega events", "מגה איוונטס"),
    "viagogo": ("ויאגוגו",), "hamenagen": ("המנגן",), "hatarbut": ("היכל התרבות",), "kikarhamusica": ("כיכר המוזיקה",),
    "xplorer": ("אקספלורר",), "livetickets": ("live tickets", "לייב טיקטס"), "tiuli": ("טיולי",), "ogo": ("אוגו",),
    "muzi": ("מוזי",), "standupist": ("סטנדאפיסט פורטל", "פורטל סטנדאפיסט"), "onecity": ("one city", "וואן סיטי"), "petahtikvamuni": ("עיריית פתח תקווה", "עיריית פתח תקוה"), "jerusalemmuni": ("עיריית ירושלים",), "sharepointmuni": ("עיריית ראשון לציון", "עיריית ראשל\"צ"), "tickethouse": ("ticket house", "טיקט האוס"),
    "barak": ("barak tickets", "ברק כרטיסים"), "bargiyora": ("bar giyora", "בר גיורא"), "levontin7": ("levontin 7", "לבונטין 7"),
    "yellowsubmarine": ("yellow submarine", "הצוללת הצהובה"), "halutz": ("hahalutz 33", "החלוץ 33"), "meety": ("מיטי",),
    "funzing": ("פאנזינג",), "ticketeams": ("טיקטימס",),
}  # fmt: skip


def _escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pattern(word: str) -> str:
    return "%" + _escape(word) + "%"


def _json_has(column: SQLColumnExpression[Any], word: str) -> Cond:
    """A JSON list column (event types) holding ``word`` (its JSONB text, case-insensitive)."""
    return cast(column, String).ilike(_pattern(word), escape="\\")


DID_YOU_MEAN_CUTOFF = 0.75  # looser than TYPO_CUTOFF: a suggestion to click, not extra results
TYPO_CUTOFF = 0.85  # difflib ratio: one letter off in a 5-6 letter word
CITY_SEARCH_POPULATION = 100_000  # smaller towns abroad share names with plain words
SHORT_WORD = 3  # up to this long, a word must stand alone in the text: "ראפ" is not in "תראפיה"


def text_match(word: str) -> Cond:
    pattern = _pattern(word)

    def found(c: SQLColumnExpression[Any]) -> Cond:
        inside = c.ilike(pattern, escape="\\")
        return and_(inside, whole_word(c, word)) if len(word) <= SHORT_WORD else inside

    # The site's own categories too: a ticket site files tennis under "טניס" without the word in the title.
    return or_(lineup_contains(word), _json_has(EventRow.event_types, word), _json_has(EventRow.source_genres, word), *(found(c) for c in (
        EventRow.title, EventRow.description, EventRow.venue_name, EventRow.city, EventRow.address, EventRow.organizer,
    )))  # fmt: skip


def _whole_word(column: SQLColumnExpression[Any], word: str) -> Cond:
    w = _escape(word)
    return or_(*(column.ilike(p, escape="\\") for p in (w, f"{w} %", f"% {w}", f"% {w} %", f"%-{w}%", f"%{w}-%")))


_HE_SOUNDS = {
    "ג'": "j", "ג׳": "j", "ז'": "z", "ז׳": "z", "צ'": "ts", "צ׳": "ts", "וו": "b",
    "ב": "b", "ג": "g", "ד": "d", "ז": "z", "ח": "k", "ט": "t", "כ": "k", "ך": "k", "ל": "l", "מ": "m", "ם": "m",
    "נ": "n", "ן": "n", "ס": "s", "פ": "p", "ף": "p", "צ": "ts", "ץ": "ts", "ק": "k", "ר": "r", "ש": "s", "ת": "t",
}  # fmt: skip
_LATIN_SOUNDS = [
    (r"tion", "sn"), (r"sh|sch", "s"), (r"ch|kh|ck|qu?|c(?![eiy])", "k"), (r"c", "s"), (r"ph|f", "p"), (r"th", "t"), (r"tz", "ts"),
    (r"x", "ks"), (r"w|v", "b"), (r"[aeiouyh]", ""),
]  # fmt: skip


def skeleton(name: str) -> str:
    """The consonants of a name in one alphabet, so a Hebrew and a Latin spelling meet:
    "אינפקטד מאשרום" and "Infected Mushroom" are both "npktdmsrm". Vowels (and א ה ו י ע) drop out."""
    text = re.sub(r"(?<![א-ת])ו", "b", name.casefold())  # a leading ו is a V ("ויני ויצ'י")
    for he, sound in _HE_SOUNDS.items():
        text = text.replace(he, sound)
    for pattern, sound in _LATIN_SOUNDS:
        text = re.sub(pattern, sound, text)
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", text))


def sounds_like(q: str, by_skeleton: dict[str, list[str]]) -> list[str]:
    """Lineup names spelled differently from ``q`` that sound the same ("אינפקטד מאשרום": Infected Mushroom).
    ponytail: a short skeleton (three consonants) counts only when one name has it; fuzzier matching
    (one consonant off) when the misses log shows a need."""
    key = skeleton(q)
    names = by_skeleton.get(key, []) if len(key) >= 3 else []
    if len(key) == 3 and len(names) > 1:
        return []
    return [n for n in names if n.casefold() != q.strip().casefold()]


def did_you_mean(q: str, cfg: Settings, names: Iterable[str]) -> str | None:
    """The known name (genre, kind, city, region, ticket site, artist) closest to a search that
    found nothing: "אינפקטד מושרום" -> "Infected Mushroom"."""
    text = " ".join(q.casefold().split())
    choices = {n.casefold(): n for n in (*_vocabulary(cfg), *names) if len(n) >= 3}
    near = get_close_matches(text, list(choices), n=1, cutoff=DID_YOU_MEAN_CUTOFF)
    return choices[near[0]] if near and near[0] != text else None


def by_skeleton(names: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in {n.strip() for n in names if n and n.strip()}:
        out.setdefault(skeleton(name), []).append(name)
    return out


TIER = 1000  # relevance(): tier * TIER - parts matched
UMBRELLA_GENRES = 4  # more genres than this: a mixed listing, one part less in relevance()


def tier(rank: int) -> int:
    """0 / 1 / 2 of a ``relevance`` value (2: the words are only in the description, address...)."""
    return (rank + TIER - 1) // TIER


def relevance(q: str, cfg: Settings, artists: Iterable[str] = ()) -> ColumnElement[int] | None:
    """Tier 0: the event is of a searched genre / kind ("טכנו", "מסיבה") or a searched word is a whole word of
    its title; 1: a word is inside the title, lineup or venue; 2: only elsewhere (description, city...).
    Within a tier, events matching more of the search come first: for "סרט לילדים" a children's film
    before a kids' workshop that only mentions film. Sorts search results before the date, so "טכנו"
    lists techno parties before "טכנודע". The value is tier * TIER - parts matched (smaller first)."""
    text = " " + " ".join(q.casefold().split()) + " "
    for name in sorted(_area_and_site_names(cfg), key=len, reverse=True):  # "גוש דן", "בתל אביב": places, not title words
        text = text.replace(f" {name} ", " ")
    # Dates, prices and "free" are filters, not title words ("הלילה" is no reason to list "מטבח לילה" first).
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    text = " " + " ".join(PRICE.sub(" ", text).split()) + " "
    for name in sorted({*FREE, *WEEKEND, *DAYS, *date_ranges(today), *exact_dates(text.strip(), today)}, key=len, reverse=True):
        text = text.replace(f" {name.casefold()} ", " ")
    words = [w for w in re.findall(r"\w{2,}", text) if not w.isdigit() and w not in FILLER]  # "מה יש" is in "ישראל"
    artists = list(artists)
    if not words and not artists:
        return None
    genres = {g for name, gs in _genre_names(cfg).items() if f" {name} " in text for g in gs}
    forms = [f for w in words for f in ((w, w[1:]) if len(w) > 3 and w[0] in "בלמהו" else (w,))]
    top = [_whole_word(EventRow.title, f) for f in forms] + [_genre(cfg, g) for g in genres]
    top += [lineup_contains(a) for a in artists]
    inside = [
        EventRow.title.ilike(_pattern(f), escape="\\") | lineup_contains(f) | EventRow.venue_name.ilike(_pattern(f), escape="\\")
        for f in forms
    ]
    # Each kind searched and each other word: one part. Words of a kind's own name count with the kind.
    named = {name for name in _genre_names(cfg) if f" {name} " in text}
    kinds = [or_(*(_genre(cfg, g) for g in _genre_names(cfg)[name])) for name in named]
    loose = [w for w in words if not any(w in name.split() for name in named)]
    parts = kinds + [or_(*(_whole_word(EventRow.title, f) for f in (w, w[1:]) if len(f) >= 2)) for w in loose]
    matched = sum((case((p, 1), else_=0) for p in parts), literal(0)) if parts else literal(0)
    if kinds:  # a listing filed under many kinds ("אירועי נובמבר": 9 genres) is less likely the kind asked for
        matched = matched - case((func.jsonb_array_length(EventRow.genres) > UMBRELLA_GENRES, 1), else_=0)
    return case((or_(*top), 0), (or_(*inside), TIER), else_=2 * TIER) - matched


def _area_and_site_names(cfg: Settings) -> set[str]:
    names = {n for key, (he, _, en) in seo.REGION_NAMES.items() for n in (he, en, *REGION_ALIASES.get(key, ()))}
    names |= {n for key in cfg.sources for n in (key, *SOURCE_ALIASES.get(key, ()))}
    # A city in Israel too: the search already keeps only its events, so "תל אביב" in a title is no merit.
    names |= {n for c in cfg.enrichment.cities for n in (c.he, c.en, *c.aliases)}
    return {p + n.casefold() for n in names for p in (HE_PREFIXES if re.match(r"[֐-׿]", n) else ("",))}


def _genre(cfg: Settings, genre: str) -> Cond:
    return or_(*(cast(EventRow.genres, String).ilike(f'%"{g}"%') for g in cfg.enrichment.expand_genres([genre])))


def _genre_names(cfg: Settings) -> dict[str, tuple[str, ...]]:
    """Casefolded name (Hebrew / English label, key, synonym) -> the genres it means."""
    names: dict[str, tuple[str, ...]] = {}
    for g in {*cfg.enrichment.genres, *seo.GENRES_HE}:
        for label in (seo.genre_label(g, "he"), seo.genre_label(g, "en"), g.replace("_", " ")):
            for part in {label, *label.split(" / "), *label.split(" ו")}:
                if len(part.strip()) >= 2:
                    names.setdefault(part.strip().casefold(), (g,))
    for name, gs in SYNONYMS.items():
        names.setdefault(name.casefold(), gs)
    for name in list(names):  # ג׳אז and ג'אז: either apostrophe
        names.setdefault(name.replace("׳", "'"), names[name])
        names.setdefault(name.replace("'", "׳"), names[name])
    return names


def _countries() -> list[tuple[str, str]]:
    """(name, code) in Hebrew and English: the app's one country index (partylinks.countries)."""
    return countries.names()


def _vocabulary(cfg: Settings) -> dict[str, Callable[[], Cond]]:
    """Casefolded name -> its filter. Built per call: a few hundred names, cheap next to the query."""
    names: dict[str, Callable[[], Cond]] = {}

    def add(name: str, cond: Callable[[], Cond]) -> None:
        name = name.strip().casefold()
        if len(name) < 2:
            return
        hebrew = bool(re.match(r"[֐-׿]", name))
        for prefix in HE_PREFIXES if hebrew else ("",):
            names.setdefault(prefix + name, cond)

    for c in cfg.enrichment.cities:  # first: a city's name wins over a country or a genre of the same name
        for name in (c.he, c.en, *c.aliases):
            add(name, lambda he=c.he: EventRow.city == he)
    for name, code in _countries():
        add(name, lambda code=code: EventRow.country == code)
    add("ישראל", lambda: EventRow.country.is_(None))
    add("israel", lambda: EventRow.country.is_(None))
    for key, (he, _, en) in seo.REGION_NAMES.items():
        for name in (he, en, en.removeprefix("the "), *REGION_ALIASES.get(key, ())):
            add(name, lambda key=key: EventRow.area == key)
    for name, genres in _genre_names(cfg).items():
        add(name, lambda genres=genres: or_(*(_genre(cfg, g) for g in genres)))
    for key in cfg.sources:  # a ticket site: everything it sells ("zygo", "גו אאוט")
        for name in (key, *SOURCE_ALIASES.get(key, ())):
            add(name, lambda key=key: EventRow.group_id.in_(select(_listing.group_id).where(_listing.source == key)))
    return names


def smart_filters(q: str, cfg: Settings, artists: Iterable[str] = ()) -> list[Cond]:
    """``artists``: lineup names that sound like ``q`` (``sounds_like``); their events match too."""
    text = " " + re.sub(r"\s+", " ", q.casefold()) + " "
    filters: list[Cond] = []

    def take(phrases: Iterable[str]) -> list[str]:
        nonlocal text
        found = []
        for p in sorted(phrases, key=len, reverse=True):
            if f" {p} " in text:
                text = text.replace(f" {p} ", " ")
                found.append(p)
        return found

    for m in PRICE.finditer(text):
        filters.append(EventRow.group_min_price <= int(m[1] or m[2]))
    text = PRICE.sub(" ", text)
    if take(FREE):
        filters.append(EventRow.group_has_free_entry.is_(True))
    if take(ABROAD):
        filters.append(EventRow.country.is_not(None))
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    if take(WEEKEND):
        filters += [EventRow.is_weekend.is_(True), EventRow.local_date <= today + timedelta(days=7)]
    for word in take(DAYS):
        filters.append(EventRow.local_date == today + timedelta(days=DAYS[word]))
    exact = exact_dates(text.strip(), today)
    for phrase in take(exact):
        filters.append(EventRow.local_date == exact[phrase])
    ranges = date_ranges(today)
    for phrase in take(ranges):
        filters.append(EventRow.local_date.between(*ranges[phrase]))
    vocab = _vocabulary(cfg)
    for name in take(vocab):
        filters.append(or_(vocab[name](), text_match(name)))  # the name in the text counts too: "Jordan" the DJ
    # A city abroad in either language finds it in both: "ברצלונה" also finds "Barcelona" (GeoNames names).
    words = text.split()
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i : i + size])
            if f" {phrase} " not in text:
                continue  # part of a longer city already taken
            for name in (phrase, phrase[1:]) if phrase[0] in "בלמהו" else (phrase,):
                if len(name) >= 3 and (city := city_abroad(name, min_population=CITY_SEARCH_POPULATION)):
                    text = text.replace(f" {phrase} ", " ")
                    filters.append(or_(*(text_match(n.casefold()) for n in city[1])))
                    break
    long_names = [n for n in vocab if len(n) >= 4 and " " not in n]
    for w in (w for w in text.split() if len(w) >= 2 and w not in FILLER):
        # "ברומא" is "רומא" with "in": a Hebrew one-letter prefix may be dropped.
        match = or_(text_match(w), text_match(w[1:])) if len(w) > 3 and w[0] in "בלמהו" else text_match(w)
        # A typo of a known name ("טכנוו", "פסטבל") also finds what the name finds.
        if len(w) >= 4 and (near := get_close_matches(w, long_names, n=1, cutoff=TYPO_CUTOFF)):
            match = or_(match, vocab[near[0]](), text_match(near[0]))
        filters.append(match)
    if not filters:
        return []
    return [or_(and_(*filters), *(lineup_contains(a) for a in artists))]
