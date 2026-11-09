"""An event's description as a reader should get it: paragraphs, headings and lists out of plain text.

Descriptions are stored as plain text, as each source wrote them. Many keep their lines; many sources
send one flat run (Makore's JSON-LD: ~10,000 events). The app (web/src/event_text) and the
server-rendered page (seo.py) both show these blocks, so a crawler reads what a visitor reads. No words
are dropped; only bullet marks move into the markup.
"""

from __future__ import annotations

import re
import unicodedata
from html import escape
from typing import Literal

from pydantic import BaseModel

from partylinks.event_text.config import EventTextSettings
from partylinks.parsing import tidy_text

Direction = Literal["rtl", "ltr"]

_EMOJI = "[\U0001f1e6-\U0001f1ff\U0001f300-\U0001faff⌚-⏿☀-➿⭐⭕]"
_STARTS_EMOJI = re.compile(_EMOJI)
_BULLET = re.compile(r"[•·▪●◦‣◆■►▶*\-–—]\s+")
_NUMBERED = re.compile(r"\d{1,2}[.)]\s")
# "דיזנגוף 143 - שישה במאי: ...", "Date: Friday": a short name, then what it is about.
_LABELLED = re.compile(r".{2,40}?(?::\s| [-–] )\S")
_URL = re.compile(r"https?://|www\.")
_HEADING_END = re.compile(r"[:?]\s*(?:\([^()]*\))?$")
_INLINE_BULLET = re.compile(r"\s+[•▪●]\s+|\s+\*(?=[^\s*])")  # " • a", or " *a" (terms run together)
_BEFORE_EMOJI = re.compile(rf"\s+(?={_EMOJI})")
_SENTENCE_END = re.compile(r"""[.!?…]+["'״”»)]*\s+(?=\S)""")
_RULE = re.compile(r"[-_=*~—─.\s]{3,}")  # a line of dashes: the gap between blocks is the divider
_BOLD_LINE = re.compile(r"\*\*([^*]+)\*\*:?")  # some sources write Markdown: this line is a title
_MD_HEADING = re.compile(r"#{1,6}\s+")  # and so is this one
_MARKDOWN = re.compile(r"\*\*([^*]+)\*\*|\[([^\]]+)\]\([^)\s]+\)")


class TextBlock(BaseModel):
    kind: Literal["p", "h", "ul"]
    dir: Direction
    text: str = ""  # p, h; a p may hold line breaks
    items: list[str] = []  # ul
    plain: bool = False  # ul whose lines open with their own mark (emoji, a name): no bullets added


class DescriptionLayout(BaseModel):
    blocks: list[TextBlock]
    collapse: bool  # long enough to open behind "read more"


def _direction(text: str) -> Direction | None:
    """The direction most of its letters run in; None when it has no letters or a tie."""
    rtl = ltr = 0
    for char in text:
        kind = unicodedata.bidirectional(char)
        rtl += kind in ("R", "AL")
        ltr += kind == "L" and char.isalpha()
    return "rtl" if rtl > ltr else "ltr" if ltr > rtl else None


def _sentences(text: str, s: EventTextSettings) -> list[str]:
    """Paragraphs of about ``paragraph_chars``, cut only between sentences. "Sgt. Pepper", "12. " and a
    following lower-case word are no sentence end; a text without any stays whole."""
    parts: list[str] = []
    start = 0
    for end in _SENTENCE_END.finditer(text):
        word_at = text.rfind(" ", start, end.start()) + 1
        if end.start() - word_at > 3 and not text[end.end()].islower() and end.end() - start >= s.paragraph_chars:
            parts.append(text[start : end.end()].strip())
            start = end.end()
    if parts and len(text) - start < s.paragraph_chars // 4:  # a last scrap stays with the paragraph before
        return [*parts[:-1], f"{parts[-1]} {text[start:]}"]
    return [*parts, text[start:]]


def _unflatten(line: str, s: EventTextSettings) -> list[str]:
    """One line back into the lines it lost: inline bullets, runs of emoji marks, then sentences."""
    bullets = _INLINE_BULLET.split(line)
    if len(bullets) > s.list_min_items:
        return [bullets[0], *(f"• {b}" for b in bullets[1:])]
    if len(line) <= s.long_line_chars:
        return [line]
    pieces: list[str] = []
    for piece in _BEFORE_EMOJI.split(line):
        if pieces and not any(c.isalpha() for c in pieces[-1]):  # "✨ 🌿 שלום": marks stay with their words
            pieces[-1] += f" {piece}"
        else:
            pieces.append(piece)
    if len(pieces) <= s.list_min_items:
        pieces = [line]
    return [part for piece in pieces for part in (_sentences(piece, s) if len(piece) > s.long_line_chars else [piece])]


def _kind(line: str, s: EventTextSettings) -> str:
    if _BULLET.match(line):
        return "bullet"
    if _NUMBERED.match(line):
        return "numbered"
    if (
        len(line) <= s.heading_chars
        and (_HEADING_END.search(line) or _BOLD_LINE.fullmatch(line) or _MD_HEADING.match(line))
        and not _URL.search(line)
    ):
        return "heading"
    if _STARTS_EMOJI.match(line):
        return "emoji"
    return "labelled" if _LABELLED.match(line) else "text"


def _blocks(lines: list[str], s: EventTextSettings, direction: Direction) -> list[TextBlock]:
    def block(
        kind: Literal["p", "h", "ul"], text: str = "", items: tuple[str, ...] | list[str] = (), plain: bool = False
    ) -> TextBlock:
        body = text or " ".join(items)
        own = _direction(body) if len(body) >= s.ltr_block_chars else None
        return TextBlock(kind=kind, dir=own or direction, text=text, items=list(items), plain=plain)

    kinds = [_kind(line, s) if line else "" for line in lines]
    last = max((i for i, line in enumerate(lines) if line), default=0)
    blocks: list[TextBlock] = []
    at = 0
    while at < len(lines):
        line, kind = lines[at], kinds[at]
        if not line:
            at += 1
            continue
        if kind in ("bullet", "numbered", "emoji", "labelled"):
            run = [at]  # blank lines between items don't end a list
            for next_at in range(at + 1, len(lines)):
                if kinds[next_at] == kind:
                    run.append(next_at)
                elif kinds[next_at]:
                    break
            if len(run) >= (2 if kind == "bullet" else s.list_min_items):
                items = [lines[i] for i in run]
                blocks.append(
                    block(
                        "ul",
                        items=[_BULLET.sub("", i, count=1) for i in items] if kind == "bullet" else items,
                        plain=kind != "bullet",
                    )
                )
                at = run[-1] + 1
                continue
        tight = len(line) <= s.tight_line_chars and at and lines[at - 1] and len(lines[at - 1]) <= s.tight_line_chars
        if kind == "heading" and at < last:
            blocks.append(block("h", _MD_HEADING.sub("", line, count=1).replace("**", "")))
        elif tight and blocks and blocks[-1].kind == "p":  # a date, a place, a lineup: stacked lines
            blocks[-1] = block("p", f"{blocks[-1].text}\n{line}")
        else:
            blocks.append(block("p", line))
        at += 1
    return blocks


def description_layout(text: str | None, s: EventTextSettings) -> DescriptionLayout | None:
    """The description's blocks, or None when it has no text."""
    tidy = tidy_text(text)
    if not tidy:
        return None
    lines = [
        part
        for line in tidy.split("\n")
        for part in (_unflatten(line, s) if line and not _RULE.fullmatch(line) else [""])
    ]
    return DescriptionLayout(blocks=_blocks(lines, s, _direction(tidy) or "rtl"), collapse=len(tidy) > s.collapse_chars)


def blocks_html(layout: DescriptionLayout | None) -> str:
    """The server-rendered description: all of it, as the app lays it out. Links stay plain text here."""

    def inline(text: str) -> str:
        return escape(_MARKDOWN.sub(lambda m: m[1] or m[2], text)).replace("\n", "<br>")

    html: list[str] = []
    for block in layout.blocks if layout else []:
        if block.kind == "ul":
            html.append(f'<ul dir="{block.dir}">' + "".join(f"<li>{inline(i)}</li>" for i in block.items) + "</ul>")
        else:
            tag = "h3" if block.kind == "h" else "p"
            html.append(f'<{tag} dir="{block.dir}">{inline(block.text)}</{tag}>')
    return "".join(html)
