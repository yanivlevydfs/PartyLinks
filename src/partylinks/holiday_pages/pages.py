"""/holiday/<key> and /holidays as crawlers see them: the holiday's events with a FAQ, and the coming holidays."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.config import Settings
from partylinks.holiday_pages.core import Holiday, where, with_pages

TEXT = {
    "he": {
        "index": "חגים: מה עושים בחגי ישראל", "index_desc": "מסיבות, הופעות ואירועים לכל חגי ישראל: חנוכה, פורים, פסח, יום העצמאות, שבועות, ראש השנה, סוכות ועוד.",
        "title": "מה עושים ב{name}? מסיבות, הופעות ואירועים", "desc": "כל האירועים ב{name} ({dates}): מסיבות, הופעות, הצגות ופעילויות לילדים, עם מחירים וכרטיסים.",
        "intro": "כל האירועים של {name}, מערב החג ועד סופו ({dates}), מכל אתרי הכרטיסים במקום אחד.", "scope": "ב{name}", "events": "אירועים",
    },
    "en": {
        "index": "Holidays: what's on for Israel's holidays", "index_desc": "Parties, concerts and events for every Israeli holiday: Hanukkah, Purim, Passover, Independence Day, Shavuot, Rosh Hashanah, Sukkot and more.",
        "title": "What's on for {name}: parties, concerts and events", "desc": "Every event for {name} ({dates}): parties, concerts, shows and kids' activities, with prices and tickets.",
        "intro": "Every event for {name}, from the eve to its last day ({dates}), from every ticketing site in one place.", "scope": "for {name}", "events": "events",
    },
}  # fmt: skip


def _dates(h: Holiday) -> str:
    return h.start.strftime("%d.%m") + (f"–{h.end.strftime('%d.%m')}" if h.end != h.start else "")


def _today(cfg: Settings) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone))


def holiday_page(session: Session, cfg: Settings, lang: str, key: str) -> seo.Page | None:
    found = next(((h, n) for h, n in with_pages(session, cfg, _today(cfg).date()) if h.key == key), None)
    if found is None:
        return None  # no such holiday, or too few events on it yet
    h, _ = found
    t, name, dates = TEXT[lang], h.name(lang), _dates(h)
    title = t["title"].format(name=name)
    rows = seo._upcoming(session, *where(h), limit=500)
    faq = seo._faq(rows, lang, t["scope"].format(name=name), cfg.site.name)
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
    }
    path = f"/holiday/{h.key}"
    faq_html = "".join(f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in faq)
    return seo.Page(
        title=f"{title} · {cfg.site.name}",
        description=t["desc"].format(name=name, dates=dates),
        path=path,
        lang=lang,
        image=seo._cover(cfg, rows[0]) if rows else None,
        jsonld=[seo._item_list(rows[: seo.LIST_LIMIT], cfg, title), faq_ld,
                seo._breadcrumbs(cfg, lang, (t["index"], "/holidays"), (title, path))],
        body=f"<h1>{escape(title)}</h1><p>{escape(t['intro'].format(name=name, dates=dates))}</p>"
        + seo._event_list_html(rows[: seo.LIST_LIMIT], cfg, lang)
        + f"<section><h2>{'שאלות נפוצות' if lang == 'he' else 'FAQ'}</h2>{faq_html}</section>",
    )  # fmt: skip


def holidays_page(session: Session, cfg: Settings, lang: str) -> seo.Page:
    t = TEXT[lang]
    items = "".join(
        f'<li><a href="/holiday/{h.key}">{escape(h.name(lang))}</a> · {_dates(h)} · {n} {t["events"]}</li>'
        for h, n in with_pages(session, cfg, _today(cfg).date())
    )
    return seo.Page(
        title=f"{t['index']} · {cfg.site.name}",
        description=t["index_desc"],
        path="/holidays",
        lang=lang,
        jsonld=[seo._breadcrumbs(cfg, lang, (t["index"], "/holidays"))],
        body=f"<h1>{escape(t['index'])}</h1><ul>{items}</ul>",
    )


def paths(session: Session, cfg: Settings) -> list[tuple[str, str, str]]:
    """(path, Hebrew name, English name) of the holiday pages, for the sitemap and the home page's links."""
    pages = with_pages(session, cfg, _today(cfg).date())
    return [("/holidays", "חגים", "Holidays"), *((f"/holiday/{h.key}", h.he, h.en) for h, _ in pages)] if pages else []
