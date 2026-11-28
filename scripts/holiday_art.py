"""Draws the holidays' own artwork (original vector art, no photos or third-party pictures) and writes it as WebP:
web/public/holidays/<key>.webp, the banner behind a holiday row's title (2400x240: wide and low as the row's head,
drawn at its right end), and <key>-og.webp, the
holiday page's sharing picture (1200x630, with its name). Run from the project root after a change:

    python scripts/holiday_art.py web
"""

from __future__ import annotations

import io
import math
import random
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright  # pyrefly: ignore[missing-import]  (a local tool, not a project dependency)

web = Path(sys.argv[1]).resolve()
out = web / "public/holidays"
fonts = web / "node_modules/@fontsource"


def stars(rnd: random.Random, w: int, h: int, n: int, colour: str = "#fff") -> str:
    return "".join(
        f'<circle cx="{rnd.uniform(0, w):.0f}" cy="{rnd.uniform(0, h):.0f}" r="{rnd.uniform(0.6, 2.2):.1f}" '
        f'fill="{colour}" opacity="{rnd.uniform(0.25, 0.9):.2f}"/>'
        for _ in range(n)
    )


def snow(rnd: random.Random, w: int, h: int, n: int) -> str:
    flakes = []
    for _ in range(n):
        x, y, r = rnd.uniform(0, w), rnd.uniform(0, h), rnd.uniform(5, 13)
        arms = "".join(
            f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{x + r * math.cos(a):.1f}" y2="{y + r * math.sin(a):.1f}"/>'
            for a in (k * math.pi / 3 for k in range(6))
        )
        flakes.append(
            f'<g stroke="#e8f4ff" stroke-width="1.6" stroke-linecap="round" opacity="{rnd.uniform(0.35, 0.8):.2f}">{arms}</g>'
        )
    return "".join(flakes)


def bulbs(w: int, sag: float, top: float, colours: list[str]) -> str:
    """A string of glowing lights across the top."""
    path = f'<path d="M0 {top} Q {w / 2} {top + sag * 2} {w} {top}" fill="none" stroke="#2a2f3a" stroke-width="3"/>'
    lights = []
    n = 22
    for i in range(1, n):
        t = i / n
        x = w * t
        y = (1 - t) ** 2 * top + 2 * (1 - t) * t * (top + sag * 2) + t**2 * top
        c = colours[i % len(colours)]
        lights.append(f'<circle cx="{x:.0f}" cy="{y + 14:.0f}" r="22" fill="{c}" opacity=".28" filter="url(#blur)"/>'
                      f'<ellipse cx="{x:.0f}" cy="{y + 14:.0f}" rx="7" ry="10" fill="{c}"/>'
                      f'<rect x="{x - 4:.0f}" y="{y:.0f}" width="8" height="6" rx="1" fill="#7a7f8c"/>')  # fmt: skip
    return path + "".join(lights)


def tree(x: float, base: float, size: float) -> str:
    tiers = []
    for i, (width, rise) in enumerate([(1.0, 0.0), (0.78, 0.26), (0.56, 0.5)]):
        y = base - size * rise
        half = size * 0.42 * width
        tiers.append(f'<path d="M{x - half:.0f} {y:.0f} L{x:.0f} {y - size * 0.42:.0f} L{x + half:.0f} {y:.0f} Z" '
                     f'fill="url(#pine)" opacity="{0.95 - i * 0.05:.2f}"/>')  # fmt: skip
    top = base - size * 0.92
    star = " ".join(
        f"{x + (18 if k % 2 == 0 else 7) * math.sin(k * math.pi / 5):.1f},{top + -(18 if k % 2 == 0 else 7) * math.cos(k * math.pi / 5):.1f}"
        for k in range(10)
    )
    garland = "".join(
        f'<circle cx="{x + size * 0.32 * math.sin(k * 1.7):.0f}" cy="{base - size * (0.1 + 0.07 * k):.0f}" r="5" fill="{c}"/>'
        for k, c in enumerate(
            [
                "#ff3b6b",
                "#ffd166",
                "#5ce1ff",
                "#ff3b6b",
                "#b6ff3b",
                "#ffd166",
                "#5ce1ff",
                "#ff9f1c",
                "#ff3b6b",
                "#ffd166",
            ]
        )
    )
    return (
        f'<rect x="{x - 12:.0f}" y="{base:.0f}" width="24" height="{size * 0.12:.0f}" fill="#5b3a1e"/>'
        + "".join(tiers)
        + garland
        + f'<circle cx="{x:.0f}" cy="{top:.0f}" r="34" fill="#ffd166" opacity=".35" filter="url(#blur)"/>'
        + f'<polygon points="{star}" fill="#ffd166"/>'
    )


def menorah(cx: float, base: float, scale: float) -> str:
    parts = [f'<rect x="{cx - 70 * scale:.0f}" y="{base:.0f}" width="{140 * scale:.0f}" height="{12 * scale:.0f}" rx="5" fill="#e8b94a"/>',
             f'<rect x="{cx - 6 * scale:.0f}" y="{base - 150 * scale:.0f}" width="{12 * scale:.0f}" height="{150 * scale:.0f}" fill="#e8b94a"/>']  # fmt: skip
    for k in range(1, 5):
        r = 34 * k * scale
        parts.append(f'<path d="M{cx - r:.0f} {base - 150 * scale:.0f} A {r:.0f} {r:.0f} 0 0 0 {cx + r:.0f} {base - 150 * scale:.0f}" '
                     f'fill="none" stroke="#e8b94a" stroke-width="{9 * scale:.0f}"/>')  # fmt: skip
    for k in range(-4, 5):
        x = cx + k * 34 * scale
        top = base - (150 + (26 if k == 0 else 0)) * scale
        parts.append(f'<rect x="{x - 6 * scale:.0f}" y="{top - 34 * scale:.0f}" width="{12 * scale:.0f}" height="{34 * scale:.0f}" rx="2" fill="#f4f1ea"/>'
                     f'<circle cx="{x:.0f}" cy="{top - 52 * scale:.0f}" r="{26 * scale:.0f}" fill="#ffb347" opacity=".35" filter="url(#blur)"/>'
                     f'<path d="M{x:.0f} {top - 70 * scale:.0f} C {x + 11 * scale:.0f} {top - 52 * scale:.0f} {x + 8 * scale:.0f} {top - 38 * scale:.0f} {x:.0f} {top - 38 * scale:.0f} '
                     f'C {x - 8 * scale:.0f} {top - 38 * scale:.0f} {x - 11 * scale:.0f} {top - 52 * scale:.0f} {x:.0f} {top - 70 * scale:.0f} Z" fill="#ffd166"/>')  # fmt: skip
    return "".join(parts)


def fireworks(rnd: random.Random, w: int, h: int, n: int) -> str:
    out = []
    for _ in range(n):
        x, y, r = rnd.uniform(60, w - 60), rnd.uniform(40, h * 0.8), rnd.uniform(40, 110)
        c = rnd.choice(["#ff2e93", "#19e3ff", "#ffd166", "#b6ff3b", "#c77dff"])
        rays = "".join(
            f'<line x1="{x + r * 0.25 * math.cos(a):.0f}" y1="{y + r * 0.25 * math.sin(a):.0f}" x2="{x + r * math.cos(a):.0f}" '
            f'y2="{y + r * math.sin(a):.0f}"/><circle cx="{x + r * 1.08 * math.cos(a):.0f}" cy="{y + r * 1.08 * math.sin(a):.0f}" r="3" fill="{c}"/>'
            for a in (k * 2 * math.pi / 16 for k in range(16))
        )
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="{c}" opacity=".12" filter="url(#blur)"/>'
                   f'<g stroke="{c}" stroke-width="2.4" stroke-linecap="round">{rays}</g>')  # fmt: skip
    return "".join(out)


def confetti(rnd: random.Random, w: int, h: int, n: int) -> str:
    return "".join(
        f'<rect x="{rnd.uniform(0, w):.0f}" y="{rnd.uniform(0, h):.0f}" width="{rnd.uniform(6, 14):.0f}" height="{rnd.uniform(3, 7):.0f}" '
        f'fill="{rnd.choice(["#ff2e93", "#19e3ff", "#ffd166", "#b6ff3b", "#c77dff"])}" transform="rotate({rnd.uniform(0, 180):.0f} '
        f'{rnd.uniform(0, w):.0f} {rnd.uniform(0, h):.0f})" opacity=".8"/>'
        for _ in range(n)
    )


def mask(x: float, y: float, s: float, colour: str, tilt: float) -> str:
    return (f'<g transform="rotate({tilt} {x} {y})"><path d="M{x - 70 * s} {y} C {x - 70 * s} {y - 40 * s} {x - 20 * s} {y - 36 * s} {x} {y - 14 * s} '
            f'C {x + 20 * s} {y - 36 * s} {x + 70 * s} {y - 40 * s} {x + 70 * s} {y} C {x + 70 * s} {y + 34 * s} {x + 24 * s} {y + 40 * s} {x} {y + 18 * s} '
            f'C {x - 24 * s} {y + 40 * s} {x - 70 * s} {y + 34 * s} {x - 70 * s} {y} Z" fill="{colour}"/>'
            f'<ellipse cx="{x - 32 * s}" cy="{y}" rx="{17 * s}" ry="{11 * s}" fill="#120a1f"/>'
            f'<ellipse cx="{x + 32 * s}" cy="{y}" rx="{17 * s}" ry="{11 * s}" fill="#120a1f"/>'
            f'<path d="M{x + 70 * s} {y} q {40 * s} {-10 * s} {60 * s} {30 * s}" stroke="#ffd166" stroke-width="{4 * s}" fill="none"/></g>')  # fmt: skip


def lantern(x: float, top: float, s: float) -> str:
    return (f'<line x1="{x}" y1="0" x2="{x}" y2="{top}" stroke="#c9a54a" stroke-width="2"/>'
            f'<circle cx="{x}" cy="{top + 50 * s}" r="{46 * s}" fill="#ffb347" opacity=".3" filter="url(#blur)"/>'
            f'<path d="M{x - 22 * s} {top + 14 * s} L{x + 22 * s} {top + 14 * s} L{x + 30 * s} {top + 50 * s} L{x + 22 * s} {top + 86 * s} '
            f'L{x - 22 * s} {top + 86 * s} L{x - 30 * s} {top + 50 * s} Z" fill="#ffcf70" stroke="#c9a54a" stroke-width="{3 * s}"/>'
            f'<path d="M{x - 14 * s} {top} L{x + 14 * s} {top} L{x + 22 * s} {top + 14 * s} L{x - 22 * s} {top + 14 * s} Z" fill="#c9a54a"/>'
            f'<path d="M{x - 22 * s} {top + 86 * s} L{x + 22 * s} {top + 86 * s} L{x} {top + 104 * s} Z" fill="#c9a54a"/>')  # fmt: skip


def scene(key: str, w: int, h: int) -> str:
    rnd = random.Random(key)
    # The drawing's place: its share of the width on a sharing picture, near the right end on a wide row banner
    # (a phone shows only that end, mirrored in Hebrew to the side away from the title).
    wide = w / h > 4

    def at(share: float, from_end: float) -> float:
        return w - h * (from_end + 1.1) if wide else w * share  # 1.1: clear of the row's arrows

    if key == "christmas":
        bg = ("#0b1638", "#26124a")
        art = (
            stars(rnd, w, h, 70)
            + snow(rnd, w, h, 26)
            + bulbs(w, 26, 6, ["#ff3b6b", "#ffd166", "#5ce1ff", "#b6ff3b", "#ff9f1c"])
        )
        art += tree(at(0.86, 0.55), h * 0.96, h * 0.82) + tree(at(0.7, 1.25), h * 1.0, h * 0.5)
    elif key == "hanukkah":
        bg = ("#08163f", "#143a8c")
        art = stars(rnd, w, h, 90, "#cfe3ff") + menorah(
            at(0.82, 0.8) if wide else min(w * 0.82, w - 175 * h / 330), h * 0.9, h / 330
        )
    elif key == "new_year":
        bg = ("#070512", "#1d0b3a")
        art = stars(rnd, w, h, 60) + fireworks(rnd, w, h, 7) + confetti(rnd, w, h, 40)
    elif key == "purim":
        bg = ("#2a0b46", "#7a1450")
        art = (
            confetti(rnd, w, h, 90)
            + mask(at(0.8, 0.8), h * 0.5, h / 300, "#ffd166", -12)
            + mask(at(0.62, 1.7), h * 0.62, h / 420, "#19e3ff", 14)
        )
    elif key == "ramadan":
        bg = ("#06142b", "#0f3a4a")
        mx = at(0.84, 0.6)
        moon = (f'<circle cx="{mx}" cy="{h * 0.4}" r="{h * 0.26}" fill="#ffe08a"/>'
                f'<circle cx="{mx + h * 0.1}" cy="{h * 0.34}" r="{h * 0.24}" fill="url(#bg)"/>')  # fmt: skip
        art = (
            stars(rnd, w, h, 90, "#fff3c4")
            + moon
            + lantern(at(0.66, 1.2), h * 0.12, h / 360)
            + lantern(at(0.56, 1.7), h * 0.3, h / 480)
        )
    else:
        raise SystemExit(f"no art for {key}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{bg[0]}"/><stop offset="1" stop-color="{bg[1]}"/></linearGradient>'
            f'<linearGradient id="pine" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2fbf71"/><stop offset="1" stop-color="#0f6b3e"/></linearGradient>'
            f'<filter id="blur"><feGaussianBlur stdDeviation="9"/></filter></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#bg)"/>{art}</svg>')  # fmt: skip


TITLES = {"christmas": "חג המולד · Christmas", "hanukkah": "חנוכה · Hanukkah", "new_year": "סילבסטר · New Year's Eve",
          "purim": "פורים · Purim", "ramadan": "רמדאן · Ramadan"}  # fmt: skip


def og(key: str) -> str:
    font = (fonts / "rubik/files/rubik-hebrew-900-normal.woff2").as_uri()
    latin = (fonts / "rubik/files/rubik-latin-900-normal.woff2").as_uri()
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:R;src:url({font});font-weight:900}} @font-face{{font-family:R;src:url({latin});font-weight:900}}
*{{margin:0}} body{{width:1200px;height:630px;overflow:hidden;position:relative;font-family:R,sans-serif}}
.t{{position:absolute;left:64px;bottom:70px;color:#fff;font-size:72px;font-weight:900;direction:rtl;
text-shadow:0 4px 30px rgb(0 0 0/.6)}} .b{{position:absolute;left:66px;bottom:36px;color:#ff2e93;font:900 30px R}}
</style></head><body>{scene(key, 1200, 630)}<div class=t>{TITLES[key]}</div><div class=b>SPOTX</div></body></html>"""


def webp(png: bytes, path: Path, quality: int) -> None:
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=quality, method=6)
    print(path.relative_to(web), path.stat().st_size // 1024, "KB")


out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    for key in TITLES:
        page = browser.new_page(viewport={"width": 2400, "height": 240})
        page.set_content(f"<body style='margin:0'>{scene(key, 2400, 240)}</body>")
        webp(page.screenshot(), out / f"{key}.webp", 78)
        page.close()
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        tmp = web / f"node_modules/.holiday-og-{key}.html"
        tmp.write_text(og(key), encoding="utf-8")
        page.goto(tmp.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        webp(page.screenshot(), out / f"{key}-og.webp", 82)
        tmp.unlink()
        page.close()
    browser.close()
