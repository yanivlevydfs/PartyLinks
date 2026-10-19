"""Generic, source-independent parsing helpers (no site-specific knowledge here)."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selectolax.lexbor import LexborHTMLParser

_WS = re.compile(r"[ \t\r\f\v]+")
# Bidi marks / zero-width characters that sources leave around Hebrew text.
_INVISIBLE = re.compile("[\x00\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_BLANK_LINES = re.compile(r"\n{3,}")


class ParseError(ValueError):
    """Raised when a payload does not have the structure a scraper expects."""


def next_data(page_html: str) -> dict[str, Any]:
    """Return the JSON payload of a Next.js page (``<script id="__NEXT_DATA__">``)."""
    node = LexborHTMLParser(page_html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise ParseError("__NEXT_DATA__ script not found")
    return json.loads(node.text())


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
        try:
            walk(json.loads(script.text()))
        except (json.JSONDecodeError, TypeError):
            continue
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
    text = tree.body.text(separator="") if tree.body else markup
    lines = (_WS.sub(" ", line).strip() for line in _INVISIBLE.sub("", text).splitlines())
    cleaned = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return cleaned or None


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
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_iso(value: Any, default_tz: str | None = None) -> datetime | None:
    """Parse an ISO-8601 string; naive values are interpreted in ``default_tz`` (else UTC).

    Anything unparseable (other formats, numbers, garbage) yields None rather than
    raising, so one odd field never drops a whole event.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(default_tz))
    return dt


def from_unix(value: Any) -> datetime | None:
    """Unix timestamp in seconds or milliseconds; invalid values yield None."""
    number = to_float(value)
    if not number:
        return None
    if number > 1e11:  # milliseconds
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and number == number else None  # drop NaN


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
