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
# Cloudflare's email obfuscation: the address XORed with the first byte, in hex. The page shows "[email protected]"
# until a script decodes it; we decode it, so the event's text keeps the real address.
_CF_EMAIL = re.compile(rb'<(a|span)\b[^>]*?data-cfemail=\\?"([0-9a-fA-F]{4,})\\?"[^>]*>.*?<\\?/\1>', re.DOTALL)
_CF_LINK = re.compile(rb"/cdn-cgi/l/email-protection#([0-9a-fA-F]{4,})")


def _cf_decode(hexed: bytes) -> bytes:
    raw = bytes.fromhex(hexed.decode())
    return bytes(b ^ raw[0] for b in raw[1:])


def cf_emails(body: bytes) -> bytes:
    """A page (HTML, or JSON carrying HTML) with Cloudflare's hidden emails written out."""
    if b"cfemail" not in body and b"email-protection" not in body:
        return body
    body = _CF_EMAIL.sub(lambda m: _cf_decode(m.group(2)), body)
    return _CF_LINK.sub(lambda m: b"mailto:" + _cf_decode(m.group(1)), body)


# A "[email protected]" a source copied as plain text (OGO from the municipality): the address is gone, so the
# placeholder goes: as a contact line ("| דוא"ל: …", "Email: …") with its label, inside a sentence ("דרך המייל
# …") it points to the organizer's page, so the sentence still reads.
_LOST_EMAIL = r"\[email[\s\xa0]protected\]"
_LOST_LABELED = re.compile(
    r"(?:\s*[|,/·•]\s*|\s+)?(?<![\w\"״'])(?:כתובת\s)?(?:דוא[\"״']?ל|אימייל|מייל|e-?mail|электронная почта)"
    rf"\s*:\s*{_LOST_EMAIL}",
    re.IGNORECASE,
)
_LOST_IN_TEXT = re.compile(_LOST_EMAIL, re.IGNORECASE)
_HEBREW = re.compile("[א-ת]")


def drop_lost_emails(text: str | None) -> str | None:
    """``text`` without the "[email protected]" placeholders whose address no page carries any more."""
    if not text or "protected]" not in text:
        return text
    text = _LOST_LABELED.sub("", text)
    return _LOST_IN_TEXT.sub(
        lambda m: (
            "(בדף המארגן)" if _HEBREW.search(text[max(0, m.start() - 40) : m.start()]) else "(on the organizer's page)"
        ),
        text,
    ).strip()


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


# A site's own icon, logo or "no picture" placeholder: never an event's picture. Seen in prod (2026-11-11):
# ticketmaster.co.il/favicon.ico on tickchak / arenas events (Berry Sakharof's page had no picture though 11
# duplicates had one), smarticket's uploads/no_pic.png, showshappening's content/logos/, viagogo's bare
# d_vgg-defaultLogo, the A.S. Bina municipal CMS's content/images/fb.png, og_image.png, w-480-no-pic.png.
_SITE_LOGO = re.compile(
    r"favicon|apple-touch-icon|\.ico(?:[?#]|$)|(?:^|[/_-])no[_-]?(?:pic|image|photo)s?\.\w+(?:[?#]|$)"
    r"|default-?logo|/content/logos/|(?:^|/)content/images/(?:fb|og_image)\.png",
    re.IGNORECASE,
)
# Cloudinary's "/d_<file>/" is only the fallback for when the picture after it is missing: viagogo's
# ".../d_vgg-defaultLogo.jpg/<transforms>/categories/<id>/<id>" was a real picture on all 40 sampled (2026-11-12).
_CLOUDINARY_FALLBACK = re.compile(r"/d_[^/]+/(?=[^/]+/.)")


def is_site_logo(url: str | None) -> bool:
    """A site's icon / logo / "no picture" placeholder rather than the event's own picture."""
    return bool(url and _SITE_LOGO.search(_CLOUDINARY_FALLBACK.sub("/", url)))


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
    tree.strip_tags(["script", "style"])  # code, not text (Herzliya's calendar script, easytix's CSS)
    for br in tree.css("br"):
        br.replace_with("\n")
    # A link named in words ("קישור", "להרשמה") keeps where it leads, as Markdown the event page shows as a
    # link (partylinks.event_text, web/src/event_text); a link that shows its own address stays as it is.
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip().replace(" ", "%20").replace(")", "%29")
        label = " ".join(a.text().split()).replace("[", "(").replace("]", ")")
        if href.startswith(("http://", "https://")) and label and not label.startswith(("http", "www.")):
            a.replace_with(f"[{label}]({href})")
    for item in tree.css("li"):
        item.insert_before("• ")  # still a list in plain text (partylinks.event_text lays it out)
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
