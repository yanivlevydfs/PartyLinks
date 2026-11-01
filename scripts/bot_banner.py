"""Renders the Telegram bot's banners, web/public/bot-banner-he.png and bot-banner-en.png (1200x630), from
HTML with the site's fonts (web/node_modules/@fontsource) and colours. Run from the project root after a
change of the wording:

    python scripts/bot_banner.py web
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

web = Path(sys.argv[1]).resolve()
fonts = web / "node_modules/@fontsource"
icon = (web / "public/icon-512.png").as_uri()

TEXT = {
    "he": {"dir": "rtl", "l1": "כל המסיבות והאירועים", "l2": "בצ׳אט אחד בטלגרם", "sub": "Every party & event in Israel, in one chat",
               "chips": ["🎵 קטגוריות", "📍 אזורים", "🎟 כרטיסים", "🔔 התראות"], "badge": "חינם · בלי הרשמה"},
    "en": {"dir": "ltr", "l1": "Every party & event", "l2": "in one Telegram chat", "sub": "כל המסיבות והאירועים בישראל, בצ׳אט אחד",
               "chips": ["🎵 Categories", "📍 Areas", "🎟 Tickets", "🔔 Alerts"], "badge": "Free · No sign-up"},
}


def face(family: str, file: str, weight: int) -> str:
    return f"@font-face{{font-family:{family};src:url({(fonts / file).as_uri()});font-weight:{weight}}}"


def html(t: dict) -> str:
    css = "".join([
        face("Rubik", "rubik/files/rubik-hebrew-900-normal.woff2", 900), face("Rubik", "rubik/files/rubik-latin-900-normal.woff2", 900),
        face("Heebo", "heebo/files/heebo-hebrew-700-normal.woff2", 700), face("Heebo", "heebo/files/heebo-latin-700-normal.woff2", 700),
        face("Heebo", "heebo/files/heebo-hebrew-500-normal.woff2", 500), face("Heebo", "heebo/files/heebo-latin-500-normal.woff2", 500),
    ])
    chips = "".join(f"<span class=chip>{c}</span>" for c in t["chips"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}
*{{margin:0;box-sizing:border-box}} html{{overflow:hidden}}
body{{width:1200px;height:630px;overflow:hidden;background:#07060c;font-family:Heebo,sans-serif;color:#fff;position:relative}}
.glow1{{position:absolute;width:700px;height:700px;left:-200px;top:-260px;background:radial-gradient(circle,rgb(255 46 147/.32),transparent 65%)}}
.glow2{{position:absolute;width:760px;height:760px;right:-260px;bottom:-380px;background:radial-gradient(circle,rgb(25 227 255/.18),transparent 65%)}}
.glow3{{position:absolute;width:640px;height:640px;right:120px;top:-300px;background:radial-gradient(circle,rgb(157 77 255/.28),transparent 65%)}}
.wrap{{position:absolute;inset:0;display:flex;align-items:center;gap:64px;padding:0 84px}}
.icon{{flex:none;width:300px;height:300px;border-radius:72px;background:url({icon}) center/cover;box-shadow:0 0 0 1px rgb(255 255 255/.08),0 30px 90px rgb(157 77 255/.45),0 0 120px rgb(255 46 147/.35)}}
.text{{flex:1;min-width:0}}
h1{{font-family:Rubik;font-weight:900;font-size:60px;line-height:1.12;white-space:nowrap;letter-spacing:-.5px}}
h1 .g{{background:linear-gradient(100deg,#ff2e93,#9d4dff 55%,#19e3ff);-webkit-background-clip:text;color:transparent;display:inline-block;padding-bottom:6px}}
.sub{{margin-top:14px;font-size:24px;font-weight:500;color:#c9c3dc}}
.chips{{margin-top:28px;display:flex;flex-wrap:wrap;gap:10px}}
.chip{{font-size:20px;font-weight:700;padding:8px 16px;border-radius:999px;background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.14)}}
.foot{{position:absolute;bottom:34px;left:84px;right:84px;display:flex;justify-content:space-between;align-items:center;font-weight:700}}
.brand{{font-family:Rubik;font-weight:900;font-size:30px;letter-spacing:2px}} .brand span{{color:#ff2e93;text-shadow:0 0 14px #ff2e93}}
.badge{{font-size:20px;padding:8px 18px;border-radius:999px;background:linear-gradient(100deg,#ff2e93,#9d4dff);box-shadow:0 0 30px rgb(255 46 147/.45)}}
</style></head><body><div class=glow1></div><div class=glow3></div><div class=glow2></div>
<div class=wrap dir="{t['dir']}"><div class=icon></div><div class=text><h1>{t['l1']}<br><span class=g>{t['l2']}</span></h1>
<div class=sub>{t['sub']}</div><div class=chips>{chips}</div></div></div>
<div class=foot dir="{t['dir']}"><span class=brand>SPOT<span>X</span>.FUN</span><span class=badge>{t['badge']}</span></div></body></html>"""


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 630})
    for lang, t in TEXT.items():
        tmp = web / f"node_modules/.bot-banner-{lang}.html"
        tmp.write_text(html(t), encoding="utf-8")
        page.goto(tmp.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(web / f"public/bot-banner-{lang}.png"))
        tmp.unlink()
    browser.close()
print("ok")
