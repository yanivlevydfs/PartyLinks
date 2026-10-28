"""Generic, source-independent parsing helpers (no site-specific knowledge here)."""

from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from datetime import UTC, datetime, time, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ftfy
import json_repair
from pydantic import AfterValidator
from selectolax.lexbor import LexborHTMLParser

_WS = re.compile(r"[ \t\r\f\v]+")
# Bidi marks / zero-width characters that sources leave around Hebrew text.
_INVISIBLE = re.compile("[\x00\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[^\S\n]+")  # any space but a line break: tab, NBSP, thin, ideographic...
_TAG = re.compile(r"</?(?:br|p|div|span|li|ul|ol|strong|b|i|em|u|h[1-6]|font|a)\b[^<>]*>", re.IGNORECASE)
_BEFORE_CLOSE = re.compile(r" +([,.;:!?)\]}])(?= |$)")  # "שלום ," -> "שלום,"; ":)" untouched
_AFTER_OPEN = re.compile(r"(^|\s)([(\[{]) +")


class ParseError(ValueError):
    """Raised when a payload does not have the structure a scraper expects."""


def next_data(page_html: str) -> dict[str, Any]:
    """Return the JSON payload of a Next.js page (``<script id="__NEXT_DATA__">``)."""
    node = LexborHTMLParser(page_html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise ParseError("__NEXT_DATA__ script not found")
    return json.loads(node.text())


def og_image(page_html: str) -> str | None:
    """A page's ``og:image``, percent-encoded; WordPress's ``admin-ajax.png`` fallback is not artwork."""
    node = LexborHTMLParser(page_html).css_first('meta[property="og:image"]')
    image = clean_str(node.attributes.get("content")) if node else None
    return quote(image, safe=":/%?=&") if image and "admin-ajax" not in image else None


def jsonld_nodes(page_html: str) -> list[dict[str, Any]]:
    """Every object found in the page's ``application/ld+json`` blocks (incl. ``@graph``)."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for script in LexborHTMLParser(page_html).css('script[type="application/ld+json"]'):
        text = script.text()
        try:
            walk(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            # Hebrew abbreviations break hand-written JSON: "ויצ"ו חיפה" (muzi, Oct 2026). Repaired, not dropped.
            walk(json_repair.loads(text) if text.strip() else None)
    return found


def schema_types(node: dict[str, Any]) -> set[str]:
    raw = node.get("@type")
    return {str(t).lower() for t in (raw if isinstance(raw, list) else [raw]) if t}


def html_to_text(value: str | None) -> str | None:
    """Convert an HTML fragment (possibly entity-escaped) to readable plain text."""
    if not value:
        return None
    markup = html.unescape(value) if "&lt;" in value else value
    tree = LexborHTMLParser(f"<div>{markup}</div>")
    for br in tree.css("br"):
        br.replace_with("\n")
    for block in tree.css("p, div, h1, h2, h3, h4, h5, h6, li"):
        block.insert_after("\n")
    return tidy_text(tree.body.text(separator="") if tree.body else markup, markup=False)


def tidy_text(value: str | None, markup: bool = True) -> str | None:
    """Text as a reader should see it, whatever the source sent. ftfy repairs the characters: broken
    encodings (mojibake), HTML entities, control characters, every kind of line break, Unicode
    normalisation. Then the layout: markup left in the text read as HTML, every kind of space (tab,
    non-breaking, thin...) one plain space, each line trimmed, no space before a comma / period or
    inside brackets, a line repeated right after itself once, at most one blank line in a row, no
    invisible marks; None when nothing is left."""
    if not value:
        return None
    text = ftfy.fix_text(value, unescape_html=True, uncurl_quotes=False, fix_character_width=False)
    if markup and _TAG.search(text):  # "<br>", "<p>" kept as text by a source: read it as HTML
        return html_to_text(text)
    lines: list[str] = []
    for raw in _INVISIBLE.sub("", text).splitlines():
        line = _AFTER_OPEN.sub(r"\1\2", _BEFORE_CLOSE.sub(r"\1", _SPACES.sub(" ", raw))).strip()
        if not (line and lines and line == lines[-1]):  # the same line twice in a row: a paste slip
            lines.append(line)
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip() or None


# A text field shown as is (descriptions): tidied whatever the source sent.
TidyText = Annotated[str | None, AfterValidator(tidy_text)]


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def to_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def zone(name: str | None) -> tzinfo:
    """ZoneInfo for ``name``; unknown or empty names fall back to UTC instead of raising."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def parse_iso(value: Any, default_tz: str | None = None) -> datetime | None:
    """Parse an ISO-8601 string; naive values are interpreted in ``default_tz`` (else UTC).

    Anything unparseable (other formats, numbers, garbage) yields None rather than
    raising, so one odd field never drops a whole event.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(default_tz))
    return dt


_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)(?:\s*([AaPp])\.?[Mm]\.?)?")


def read_clock(value: Any) -> time | None:
    """The time of day written anywhere in ``value``: "21:00", "20:30 ", "8:50 PM", "בשעה 19:30";
    None only when it names none (then the source gave a date alone)."""
    found = _CLOCK.search(str(value or ""))
    if not found:
        return None
    hour, minute, half = int(found[1]), int(found[2]), (found[3] or "").lower()
    if half and 1 <= hour <= 12:
        hour = hour % 12 + (12 if half == "p" else 0)
    return time(hour, minute)


def has_time(value: Any) -> bool:
    """False for a date alone ("2026-10-30"): the source gives no time of day."""
    return not (isinstance(value, str) and len(value.strip()) == 10)


def from_unix(value: Any) -> datetime | None:
    """Unix timestamp in seconds or milliseconds; invalid values yield None."""
    number = to_float(value)
    if not number:
        return None
    if number > 1e11:  # milliseconds
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and not math.isnan(number) else None


_SLUG_DROP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SEP = re.compile(r"[\s_-]+")


def slugify(value: str | None, max_length: int = 80) -> str:
    """URL-friendly slug that keeps non-Latin letters (Hebrew stays Hebrew)."""
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = _SLUG_SEP.sub("-", _SLUG_DROP.sub(" ", text)).strip("-")
    return text[:max_length].rstrip("-")


def clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = _INVISIBLE.sub("", str(value)).strip()
    return text or None
