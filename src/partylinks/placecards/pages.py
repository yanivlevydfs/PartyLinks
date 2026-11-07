"""/places as crawlers see it: the place categories with their counts, linked to their genre pages and /now."""

from __future__ import annotations

from html import escape

from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.config import Settings
from partylinks.placecards.routes import place_categories

TEXT = {
    "he": {
        "title": "מקומות: האפי האוור, מסעדות שף, חדרי בריחה ועוד",
        "desc": "מדריך מקומות בישראל: האפי האוורים, מסעדות שף, מקומות כשרים, חדרי בריחה, מקומות לעבוד מהם ועוד, עם שעות, דילים ומה פתוח עכשיו.",
        "places": "מקומות", "open_now": "פתוחים עכשיו", "now": "מה פתוח עכשיו",
    },
    "en": {
        "title": "Places: happy hours, chef restaurants, escape rooms and more",
        "desc": "A guide to places in Israel: happy hours, chef restaurants, kosher places, escape rooms, work spaces and more, with hours, deals and what's open now.",
        "places": "places", "open_now": "open now", "now": "What's open now",
    },
}  # fmt: skip


def places_page(session: Session, cfg: Settings, lang: str) -> seo.Page:
    t = TEXT[lang]
    data = place_categories(session, cfg)
    pages = seo.genre_counts(session, cfg)  # only genres with a page of their own are linked

    def name(genre: str) -> str:
        label = escape(seo.genre_label(genre, lang))
        return f'<a href="/genre/{escape(genre)}">{label}</a>' if genre in pages else label

    items = "".join(
        f"<li>{name(c.genre)} · {c.places} {t['places']}"
        + (f" · {c.open_now} {t['open_now']}" if c.open_now else "") + "</li>"
        for c in data.categories
    )
    now = f'<p><a href="/now">{escape(t["now"])}</a> · {data.open_now} {t["open_now"]}</p>'
    return seo.Page(
        title=f"{t['title']} · {cfg.site.name}",
        description=t["desc"],
        path="/places",
        lang=lang,
        jsonld=[seo._breadcrumbs(cfg, lang, (t["title"], "/places"))],
        body=f"<h1>{escape(t['title'])}</h1>{now}<ul>{items}</ul>",
    )
