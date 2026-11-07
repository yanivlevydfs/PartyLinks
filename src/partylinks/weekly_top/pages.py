"""/top as crawlers see it: this week's most wanted events (ItemList) and the rising genres."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.config import Settings
from partylinks.weekly_top.core import rising, top

TEXT = {
    "he": {"title": "הכי מבוקשים השבוע: המסיבות וההופעות החמות", "desc": "האירועים הכי מבוקשים בשבוע הקרוב בישראל, לפי שמירות, צפיות ומספר אתרי הכרטיסים שמוכרים אותם, והז'אנרים העולים.",
           "rising": "הז'אנרים העולים", "new": "אירועים חדשים"},
    "en": {"title": "Most wanted this week: the hottest parties and shows", "desc": "The most wanted events in Israel this week, by saves, views and how many ticketing sites sell them, plus the rising genres.",
           "rising": "Rising genres", "new": "new events"},
}  # fmt: skip


def top_page(session: Session, cfg: Settings, lang: str) -> seo.Page | None:
    now = datetime.now(ZoneInfo(cfg.timezone))
    rows = top(session, cfg, now)
    if not rows:
        return None
    t = TEXT[lang]
    up = "".join(f"<li>{escape(seo.genre_label(g, lang))} · {n} {t['new']}</li>" for g, n in rising(session, cfg, now))
    return seo.Page(
        title=f"{t['title']} · {cfg.site.name}",
        description=t["desc"],
        path="/top",
        lang=lang,
        image=seo._cover(cfg, rows[0]),
        jsonld=[seo._item_list(rows, cfg, t["title"]), seo._breadcrumbs(cfg, lang, (t["title"], "/top"))],
        body=f"<h1>{escape(t['title'])}</h1>{seo._event_list_html(rows, cfg, lang)}"
        + (f"<h2>{escape(t['rising'])}</h2><ul>{up}</ul>" if up else ""),
    )
