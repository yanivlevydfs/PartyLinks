"""Venue names and addresses that some sites send in Arabic script, shown in Hebrew.

Tzkrti, Eventati, OGo, Bonire and others pass on a place as its organiser wrote it: "فندق رمادا"
(the Ramada hotel, Nazareth), "(مطعم مندولين) כניסה ראשית, עיילבון". Everything the site shows is in
Hebrew, so before a venue name or address is used (slug, area, map, dedup) it goes through
``enrichment.hebrew_places`` (config/settings.yaml):

* ``names``: an Arabic-script name or place word -> its Hebrew name (the name the same place already has
  in our other sources where there is one, so the two spellings meet as one venue). Whole words only,
  longest first, ignoring Arabic diacritics and the elongation mark.
* ``drop_beside_other``: Arabic script the site wrote beside its own Hebrew or Latin text is a translation
  of it ("Mazkeka מזקקה مزكيكا", "(فندق الأمباسدور) Nablus Rd 5"): dropped.
* A name wholly in Arabic script that ``names`` does not cover in full is not shown (None; enrichment keeps
  the original in ``extra["arabic_venue"]`` / ``extra["arabic_address"]`` so it can be added to ``names``):
  mapping only its town word ("مسرح الميدان حيفا" -> "חיפה") would make every unlisted venue there one.

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
        self._hebrew_words = {w for v in self._names.values() for w in v.split()}
        keys = sorted(self._names, key=len, reverse=True)
        words = "|".join(r"\s+".join(map(re.escape, k.split())) for k in keys)
        self._pattern = re.compile(f"(?<![{_ARABIC}])(?:{words})(?![{_ARABIC}])") if keys else None

    def __call__(self, text: str | None) -> str | None:
        """The Hebrew text; None: wholly Arabic and not fully known (not shown)."""
        if not text or not _HAS_ARABIC.search(text):
            return text
        out = _MARKS.sub("", text).translate(_PUNCT)
        beside = bool(_OTHER_LETTERS.search(_ARABIC_WORD.sub(" ", out)))  # the site's own Hebrew / Latin
        if self._pattern:
            mapped = self._pattern.sub(lambda m: self._names[_norm(m.group(0))], out)
            # "Haifa حيفا חיפה" -> "Haifa חיפה חיפה" -> "Haifa חיפה": only a mapped word said twice ("10 10" stays).
            out = re.sub(
                r"(?<!\S)(\S+)(?:\s+\1)+(?!\S)",
                lambda m: m.group(1) if m.group(1) in self._hebrew_words else m.group(0),
                mapped,
            )
        if _HAS_ARABIC.search(out):
            # Left in Arabic: a translation beside the site's own Hebrew / Latin goes; a wholly Arabic name that
            # is not fully known is not shown (a town word alone would make every unlisted venue there one).
            if not beside:
                return None
            if self.settings.drop_beside_other:
                out = _ARABIC_WORD.sub(" ", out)
        return _tidy(out) or None
