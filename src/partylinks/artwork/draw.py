"""Draw an event's card when it has no picture: its name (the largest text), every ticket site selling it,
day, time and city, SPOTX, on one of ~200 backgrounds chosen by the event's id (the same each time)."""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from bidi import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 960  # the cards' 4:3
PAD = 72
FONT = Path(__file__).with_name("fonts") / "Heebo.ttf"  # OFL, fonts/OFL.txt
NAMES_FILE = Path(__file__).resolve().parents[3] / "web" / "src" / "sourceNames.json"
HEBREW = re.compile(r"[֐-׿]")
MAX_STORES = 4
DAYS = {
    "he": ["יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳", "שבת", "יום א׳"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
# (base, glow, glow): bright duotones in the site's family. No lime: beside violet it turns to mud.
PALETTES = [
    ((12, 4, 20), (255, 46, 147), (157, 77, 255)),  # pink / violet (the site)
    ((4, 8, 24), (25, 227, 255), (60, 90, 255)),  # cyan / blue
    ((18, 6, 10), (255, 140, 50), (255, 46, 147)),  # orange / pink
    ((10, 4, 24), (230, 60, 255), (60, 110, 255)),  # magenta / blue
    ((4, 14, 18), (20, 220, 190), (25, 160, 255)),  # teal / cyan
    ((20, 4, 8), (255, 70, 70), (255, 170, 40)),  # red / orange
    ((8, 6, 24), (140, 80, 255), (25, 227, 255)),  # violet / cyan
    ((14, 4, 16), (255, 60, 160), (40, 210, 255)),  # pink / cyan
]


@cache
def store_names() -> dict[str, str]:
    """Ticket sites' display names, shared with the website (web/src/sourceNames.json)."""
    try:
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except OSError:
        return {}


@cache
def _font(size: int, weight: str) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_name(weight)
    return f


def _visual(text: str) -> str:
    """Hebrew in drawing order (Pillow here draws left to right, without a bidi engine)."""
    return get_display(text) if HEBREW.search(text) else text


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int, lines: int) -> list[str]:
    rows: list[str] = []
    cur = ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if draw.textlength(_visual(test), font=font) <= width or not cur:
            cur = test
        else:
            rows.append(cur)
            cur = word
    if cur:
        rows.append(cur)
    if len(rows) > lines:
        rows = rows[:lines]
        rows[-1] = rows[-1].rstrip(" .,-|") + "…"
    return rows


def _background(seed: int) -> tuple[Image.Image, tuple[int, int, int]]:
    """Palette, glow places and texture each from their own part of the seed."""
    base, g1, g2 = PALETTES[seed % len(PALETTES)]
    seed //= len(PALETTES)
    img = Image.new("RGB", (W, H), base)
    # The glows, drawn and blurred at a quarter size, then scaled up: the same soft light, ~10x faster.
    q = 4
    glow = Image.new("RGB", (W // q, H // q), base)
    d = ImageDraw.Draw(glow)
    x1, y1 = seed % 700 - 100, (seed // 7) % 400 - 100
    d.ellipse(((x1 - 320) / q, (y1 - 320) / q, (x1 + 620) / q, (y1 + 620) / q), fill=g1)
    x2, y2 = W - (seed // 11) % 600, H - (seed // 13) % 400
    d.ellipse(((x2 - 600) / q, (y2 - 520) / q, (x2 + 320) / q, (y2 + 320) / q), fill=g2)
    glow = glow.filter(ImageFilter.GaussianBlur(160 / q)).resize((W, H), Image.Resampling.BICUBIC)
    img = Image.blend(img, glow, 0.62)
    texture = Image.new("L", (W, H), 0)
    t = ImageDraw.Draw(texture)
    motif = (seed // 17) % 3
    if motif == 0:  # diagonal lines
        for x in range(-H, W, 22):
            t.line((x, H, x + H, 0), fill=20, width=2)
    elif motif == 1:  # dot grid
        for x in range(12, W, 30):
            for y in range(12, H, 30):
                t.ellipse((x - 2, y - 2, x + 2, y + 2), fill=34)
    else:  # rings from a corner
        cx, cy = (W + 80, -80) if seed % 2 else (-80, H + 80)
        for r in range(140, 1700, 70):
            t.ellipse((cx - r, cy - r, cx + r, cy + r), outline=26, width=3)
    img = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), img, texture)
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.5))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade), g1


def draw(
    *, event_id: str, title: str, sources: list[str], start_at: datetime | None, city: str | None,
    time_known: bool = True, timezone: str = "Asia/Jerusalem",
) -> Image.Image:  # fmt: skip
    seed = int(hashlib.sha1(event_id.encode()).hexdigest(), 16)
    img, accent = _background(seed)
    d = ImageDraw.Draw(img)
    rtl = bool(HEBREW.search(title))
    lang = "he" if rtl else "en"
    edge, align = (W - PAD, "r") if rtl else (PAD, "l")

    # Every ticket site selling it, as pills along the top.
    names = store_names()
    stores = [names.get(s, s.title()) for s in dict.fromkeys(sources)]
    pills = stores[:MAX_STORES] + ([f"+{len(stores) - MAX_STORES}"] if len(stores) > MAX_STORES else [])
    small = _font(36, "ExtraBold")
    x = edge
    for name in pills:
        width = d.textlength(_visual(name), font=small) + 48
        x0 = x - width if rtl else x
        if (rtl and x0 < PAD) or (not rtl and x0 + width > W - PAD):
            break  # no room for more
        d.rounded_rectangle((x0, PAD, x0 + width, PAD + 64), radius=32, fill=(255, 255, 255))
        d.text((x0 + width / 2, PAD + 32), _visual(name), font=small, fill=(10, 6, 18), anchor="mm")
        x = x0 - 14 if rtl else x0 + width + 14

    # The event's name: the largest text, up to three lines.
    size, big, rows = 112, _font(112, "Black"), [title]
    for size in (112, 100, 88, 78, 68, 60):
        big = _font(size, "Black")
        rows = _wrap(d, title.strip(), big, W - 2 * PAD, 3)
        if all(d.textlength(_visual(r), font=big) <= W - 2 * PAD for r in rows):
            break
    step = int(size * 1.12)
    y = H // 2 - len(rows) * step // 2 + 10
    for row in rows:
        d.text((edge, y), _visual(row), font=big, fill=(255, 255, 255), anchor=align + "a")
        y += step

    # Accent rule, then day, time and city.
    d.rounded_rectangle(
        (edge - 120 if rtl else edge, H - 196, edge if rtl else edge + 120, H - 188), radius=4, fill=accent
    )
    when = ""
    if start_at is not None:
        local = start_at.astimezone(ZoneInfo(timezone))
        when = f"{DAYS[lang][local.weekday()]} {local.day}.{local.month}" + (f" · {local:%H:%M}" if time_known else "")
    line = " · ".join(part for part in (when, city or "") if part)
    d.text((edge, H - 150), _visual(line), font=_font(44, "Medium"), fill=(230, 222, 250), anchor=align + "a")

    # SPOTX in the opposite corner.
    mark = _font(34, "Black")
    sx = PAD if rtl else W - PAD - d.textlength("SPOTX", font=mark)
    d.text((sx, H - 140), "SPOT", font=mark, fill=(255, 255, 255))
    d.text((sx + d.textlength("SPOT", font=mark), H - 140), "X", font=mark, fill=(255, 46, 147))
    return img


def webp(img: Image.Image, width: int, quality: int = 80) -> bytes:
    if width != img.width:
        img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=quality, method=5)
    return out.getvalue()
