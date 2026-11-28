"""Venue names and addresses that some sites send in Arabic script, shown in Hebrew.

Tzkrti, Eventati, OGo, Bonire and others pass on a place as its organiser wrote it: "فندق رمادا"
(the Ramada hotel, Nazareth), "(مطعم مندولين) כניסה ראשית, עיילבון". Everything the site shows is in
Hebrew, so before a venue name or address is used (slug, area, map, dedup) it goes through
``enrichment.hebrew_places`` (config/settings.yaml):

* ``names``: an Arabic-script name or place word -> its Hebrew name (the name the same place already has
  in our other sources where there is one, so the two spellings meet as one venue). Whole words only,
  longest first, ignoring Arabic diacritics and the elongation mark.
* ``drop_beside_other``: what is still in Arabic script beside Hebrew or Latin text is a translation of
  it ("Mazkeka מזקקה مزكيكا", "(فندق الأمباسدور) Nablus Rd 5"): dropped. A name wholly in Arabic script
  that ``names`` does not know is kept as it is (nothing else names the place).

Titles and descriptions are not touched: free text is never machine-translated.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_ARABIC = "\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff"
_HAS_ARABIC = re.compile(f"[{_ARABIC}]")
_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")  # diacritics, tatweel
_ARABIC_WORD = re.compile(f"[{_ARABIC}]+")
_OTHER_LETTERS = re.compile("[A-Za-z\u00c0-\u024f\u0590-\u05ff]")  # Latin, Hebrew
_PUNCT = str.maketrans({"\u060c": ",", "\u061b": ";", "\u061f": "?"})  # Arabic comma, semicolon, question mark
_SEP = "[,;|/&\\-\u2013\u2014\u00b7]"


class HebrewPlacesSettings(BaseModel):
    # Arabic-script venue name or place word -> its Hebrew name.
    names: dict[str, str] = Field(default_factory=dict)
    # Arabic script left beside Hebrew / Latin text (a translation of it) is dropped.
    drop_beside_other: bool = True

    @field_validator("names")
    @classmethod
    def _names(cls, value: dict[str, str]) -> dict[str, str]:
        empty = [k for k, v in value.items() if not k.strip() or not v.strip()]
        if empty:
            raise ValueError(f"hebrew_places.names: empty name or Hebrew name: {empty}")
        return value


def _norm(text: str) -> str:
    return " ".join(_MARKS.sub("", text).translate(_PUNCT).split())


def _tidy(text: str) -> str:
    """Punctuation left where words were dropped: "( )", ", ,", "- -", a separator at either end."""
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    text = re.sub(rf"({_SEP})(?:\s*{_SEP})+", r"\1", text)
    text = re.sub(r"\s+(?=[,)\]])|(?<=[(\[])\s+", "", text)
    return " ".join(text.split()).strip(" ,;|&-\u2013\u2014\u00b7")


class HebrewPlaces:
    """Make a venue name / address Hebrew (see the module doc)."""

    def __init__(self, settings: HebrewPlacesSettings) -> None:
        self.settings = settings
        self._names = {_norm(k): v.strip() for k, v in settings.names.items()}
        keys = sorted(self._names, key=len, reverse=True)
        words = "|".join(r"\s+".join(map(re.escape, k.split())) for k in keys)
        self._pattern = re.compile(f"(?<![{_ARABIC}])(?:{words})(?![{_ARABIC}])") if keys else None

    def __call__(self, text: str | None) -> str | None:
        if not text or not _HAS_ARABIC.search(text):
            return text
        out = _MARKS.sub("", text).translate(_PUNCT)
        if self._pattern:
            mapped = self._pattern.sub(lambda m: self._names[_norm(m.group(0))], out)
            if mapped != out:  # "Haifa حيفا חיפה" -> "Haifa חיפה חיפה" -> "Haifa חיפה"
                out = re.sub(r"(?<!\S)(\S+)(?:\s+\1)+(?!\S)", r"\1", mapped)
        if _HAS_ARABIC.search(out) and self.settings.drop_beside_other:
            rest = _ARABIC_WORD.sub(" ", out)
            if _OTHER_LETTERS.search(rest):
                out = rest
        return _tidy(out) or text
