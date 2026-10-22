"""Helpers for sources that sell events abroad: country names to ISO codes, local time zones.

Israeli sellers of shows abroad give the country as a Hebrew or English name (or a code) and the
start as the venue's local time without an offset; these turn both into what ``ScrapedEvent``
needs (ISO country, tz-aware start).

Country names come from pycountry (ISO 3166 names, official names, alpha-3 codes and their
Hebrew translations); time zones from airportsdata (every airport's city, country and zone).
"""

from __future__ import annotations

import gettext
from collections import Counter, defaultdict
from functools import cache

import airportsdata
import pycountry

from partylinks.enrichment import UNKNOWN_COUNTRY

# Names the sites use that ISO 3166 doesn't: parts of a country, cities standing for it, short forms.
ALIASES: dict[str, str] = {
    **dict.fromkeys(
        ["uk", "england", "scotland", "wales", "northern ireland", "great britain", "britain",
         "אנגליה", "בריטניה", "סקוטלנד", "ויילס", "וויילס", "אירלנד הצפונית"],
        "GB",
    ),
    **dict.fromkeys(["usa", "us", 'ארה"ב', "ארהב"], "US"),
    **dict.fromkeys(["uae", "dubai", "abu dhabi", "איחוד האמירויות", "דובאי", "אבו דאבי"], "AE"),
    **dict.fromkeys(["holland", "the netherlands"], "NL"),
    **dict.fromkeys(["czechia", "czech republic", "צכיה", "צ'כיה"], "CZ"),
    **dict.fromkeys(["turkey", "türkiye"], "TR"),
    "שווייץ": "CH",
}  # fmt: skip


def _key(value: str) -> str:
    return value.strip().strip(".").casefold()


@cache
def _countries() -> dict[str, str]:
    """Every known name or code of every country -> alpha-2."""
    hebrew = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["he"], fallback=True)
    names: dict[str, str] = {}
    for c in pycountry.countries:
        for name in (c.alpha_2, c.alpha_3, c.name, getattr(c, "common_name", None), getattr(c, "official_name", None)):
            if name:
                names[_key(name)] = c.alpha_2
                names.setdefault(_key(hebrew.gettext(name)), c.alpha_2)
    return names | {_key(k): v for k, v in ALIASES.items()}


def country_code(value: str | None) -> str:
    """ISO alpha-2 code for a country name (Hebrew or English) or code; ``UNKNOWN_COUNTRY`` if unknown."""
    return _countries().get(_key(value or ""), UNKNOWN_COUNTRY)


@cache
def _zones() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """(city, country) -> zone, and country -> zone, from the airports' own zones (most common wins)."""
    by_city: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_country: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for airport in airportsdata.load("IATA").values():
        by_city[_key(airport["city"]), airport["country"]][airport["tz"]] += 1
        by_country[airport["country"]][airport["tz"]] += 1
    return (
        {k: c.most_common(1)[0][0] for k, c in by_city.items()},
        {k: c.most_common(1)[0][0] for k, c in by_country.items()},
    )


def local_zone(country: str | None, default: str, city: str | None = None) -> str:
    """Time zone name of a venue in ``city`` (English name) of ``country`` (ISO), else the country's
    main zone, else ``default``."""
    # ponytail: a country's main zone = its airports' most common one; in multi-zone countries
    # (US, CA, AU, RU) only a matched English city name is exact. Hebrew city names need a map.
    by_city, by_country = _zones()
    return by_city.get((_key(city or ""), country or "")) or by_country.get(country or "", default)
