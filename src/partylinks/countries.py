"""Every country, built once per process from pycountry (ISO 3166 names, official names, alpha-3 codes
and their Hebrew translations) and shared by everything that names a country: sources abroad
(``country_code``), the landing pages (``country_name``), the search (``names``).

The only module that imports pycountry: one index, one Hebrew translation, loaded once.
"""

from __future__ import annotations

import gettext
import threading
from functools import cache

import pycountry

from partylinks.enrichment import UNKNOWN_COUNTRY

# Names the sites use that ISO 3166 doesn't: parts of a country, cities standing for it, short forms.
ALIASES: dict[str, str] = {
    **dict.fromkeys(
        ["uk", "england", "scotland", "wales", "northern ireland", "great britain", "britain",
         "אנגליה", "בריטניה", "סקוטלנד", "ויילס", "וויילס", "ווילס", "אירלנד הצפונית"],
        "GB",
    ),
    **dict.fromkeys(["usa", "us", 'ארה"ב', "ארהב"], "US"),
    **dict.fromkeys(["uae", "dubai", "abu dhabi", "איחוד האמירויות", "איחוד אמירויות ערביות", "דובאי", "אבו דאבי"], "AE"),
    **dict.fromkeys(["holland", "the netherlands"], "NL"),
    **dict.fromkeys(["czechia", "czech republic", "צכיה", "צ'כיה"], "CZ"),
    **dict.fromkeys(["turkey", "türkiye"], "TR"),
    **dict.fromkeys(["שווייץ", "שוויץ"], "CH"),
    "נורבגיה": "NO",
}  # fmt: skip

_loading = threading.Lock()  # sources run on their own threads: the first caller builds, the rest wait


def _key(value: str) -> str:
    return value.strip().strip(".").casefold()


@cache
def _hebrew_cached() -> gettext.NullTranslations:
    return gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["he"], fallback=True)


def _hebrew() -> gettext.NullTranslations:
    with _loading:
        return _hebrew_cached()


@cache
def _index_cached() -> dict[str, str]:
    hebrew = _hebrew_cached()
    index: dict[str, str] = {}
    for c in pycountry.countries:
        for name in (c.alpha_2, c.alpha_3, c.name, getattr(c, "common_name", None), getattr(c, "official_name", None)):
            if name:
                index[_key(name)] = c.alpha_2
                index.setdefault(_key(hebrew.gettext(name)), c.alpha_2)
    return index | {_key(k): v for k, v in ALIASES.items()}


def _index() -> dict[str, str]:
    """Every known name or code of every country (English, Hebrew, aliases) -> alpha-2."""
    with _loading:
        return _index_cached()


def country_code(value: str | None) -> str:
    """ISO alpha-2 code for a country name (Hebrew or English) or code; ``UNKNOWN_COUNTRY`` if unknown."""
    return _index().get(_key(value or ""), UNKNOWN_COUNTRY)


def is_country(code: str | None) -> bool:
    return bool(code) and pycountry.countries.get(alpha_2=code) is not None


def country_name(code: str, lang: str) -> str:
    """A country's name in Hebrew or English (its common name: "Taiwan", not the official one)."""
    c = pycountry.countries.get(alpha_2=code)
    if c is None:
        return code
    return _hebrew().gettext(c.name) if lang == "he" else getattr(c, "common_name", None) or c.name


@cache
def names() -> list[tuple[str, str]]:
    """(name, code) of every country in Hebrew and English, for the search's vocabulary."""
    return [
        (name, c.alpha_2)
        for c in pycountry.countries
        for name in {country_name(c.alpha_2, "he"), country_name(c.alpha_2, "en"), c.name}
    ]
