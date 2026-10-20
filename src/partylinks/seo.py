"""Search, social and AI visibility for the React site.

The site renders in the browser, but crawlers, link previews and LLM fetchers often don't run
JavaScript. So every public page is served from here with its own ``<head>`` (title, description,
canonical, hreflang, Open Graph / Twitter, local geo tags), schema.org JSON-LD, and a plain-HTML
copy of the content inside ``#root`` that React replaces on load. Also serves ``sitemap.xml``,
``robots.txt`` and ``llms.txt``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import ColumnElement, String, cast, or_, select
from sqlalchemy.orm import Session, selectinload

from partylinks.config import CitySettings, Settings
from partylinks.db import EventRow, upcoming_filter

LIST_LIMIT = 60  # events listed on index pages
LLMS_LIMIT = 400
DESC_LIMIT = 300
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
    "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "CCBot", "Bingbot",
]  # fmt: skip

# Operational / private endpoints crawlers have no business indexing.
ROBOTS_PRIVATE = ["/admin", "/admin/api/", "/docs", "/redoc", "/openapi.json", "/health", "/runs", "/sources",
                  "/maintenance", "/changes"]

CURRENCY_SYMBOLS = {"ILS": "₪", "EUR": "€", "USD": "$", "GBP": "£"}

TEXT: dict[str, dict[str, str]] = {
    "he": {
        "site_title": "Pulse פולס · כל האירועים בישראל",
        "site_desc": "כל האירועים, המסיבות וההופעות בכל הארץ במקום אחד: תאריכים, ליינאפ, מחירי כרטיסים, כניסה חופשית, מקומות ומפה. מתעדכן כל הזמן.",
        "upcoming": "אירועים קרובים בכל הארץ",
        "venues": "מקומות אירועים בכל הארץ",
        "artists": "אמנים ו־DJs בישראל",
        "map": "מפת האירועים של ישראל",
        "abroad": "הופעות ופסטיבלים בחו״ל",
        "abroad_desc": "כרטיסים וחבילות להופעות ופסטיבלים בחו״ל: תאריכים, מקומות ומחירים.",
        "venue_desc": "אירועים קרובים ב{name}: תאריכים, ליינאפ וכרטיסים.",
        "artist_desc": "האירועים וההופעות הקרובים של {name}: תאריכים, מקומות וכרטיסים.",
        "free": "כניסה חופשית",
        "from": "החל מ־{p}",
        "lineup": "ליינאפ",
        "tickets": "לכרטיסים",
        "not_found": "העמוד לא נמצא",
        "locale": "he_IL",
    },
    "en": {
        "site_title": "Pulse · Every event in Israel",
        "site_desc": "Every event, party and show in Israel in one place: dates, lineups, ticket prices, free entry, venues and a map. Always up to date.",
        "upcoming": "Upcoming events in Israel",
        "venues": "Event venues in Israel",
        "artists": "Artists & DJs playing Israel",
        "map": "Israel events map",
        "abroad": "Concerts and festivals abroad",
        "abroad_desc": "Tickets and packages for concerts and festivals abroad: dates, venues and prices.",
        "venue_desc": "Upcoming events at {name}: dates, lineups and tickets.",
        "artist_desc": "{name}'s upcoming events and shows in Israel: dates, venues and tickets.",
        "free": "Free entry",
        "from": "From {p}",
        "lineup": "Lineup",
        "tickets": "Tickets",
        "not_found": "Page not found",
        "locale": "en_US",
    },
}
DAYS = {
    "he": ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


# Landing pages answer what people actually search for ("מה עושים הערב", "מסיבות טכנו") with a live
# list and an FAQ whose answers are computed from the listings, so search and AI engines can quote them.
GENRES_HE = {
    "techno": "טכנו", "house": "האוס", "trance": "טראנס", "hip_hop": "היפ הופ", "rnb": "R&B", "mainstream": "מיינסטרים",
    "pop": "פופ", "reggaeton_latin": "רגאטון / לטיני", "afro": "אפרו", "dancehall_reggae": "דאנסהול / רגאיי",
    "mizrahi": "מזרחית", "israeli": "מוזיקה ישראלית", "greek": "יוונית", "arabic": "ערבית", "disco_funk": "דיסקו / פאנק",
    "drum_and_bass": "דראם אנד בייס", "bass_dubstep": "בייס / דאבסטפ", "hardstyle": "הארדסטייל", "edm": "EDM",
    "electronic": "מוזיקה אלקטרונית", "downtempo_chill": "צ׳יל", "throwback": "נוסטלגיה", "rock_indie": "רוק / אינדי",
    "jazz": "ג׳אז", "world_balkan": "בלקן / וורלד", "live_show": "הופעות חיות", "lgbt": "גאווה", "stand_up": "סטנדאפ",
    "karaoke": "קריוקי",
}  # fmt: skip
GENRES_EN_FIX = {"rnb": "R&B", "edm": "EDM", "hip_hop": "Hip Hop", "lgbt": "LGBTQ+", "reggaeton_latin": "Reggaeton / Latin",
                 "stand_up": "Stand-up comedy", "live_show": "Live music"}  # fmt: skip
GENRE_PAGE_MIN = 3  # fewer upcoming events than this: no landing page (thin content)


def genre_label(genre: str, lang: str) -> str:
    if lang == "he":
        return GENRES_HE.get(genre, genre)
    return GENRES_EN_FIX.get(genre, genre.replace("_", " ").title())


@dataclass
class Page:
    title: str
    description: str
    path: str  # canonical path, without ?lang
    lang: str
    image: str | None = None
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    body: str = ""
    noindex: bool = False
    status: int = 200
    og_type: str = "website"
    langs: tuple[str, ...] = ("he", "en")  # languages the content exists in (blog posts: Hebrew only)


# --------------------------------------------------------------------------- helpers
def _url(cfg: Settings, path: str, lang: str = "he") -> str:
    return f"{cfg.site.base_url}{path}" + ("?lang=en" if lang == "en" else "")


def event_path(row: EventRow) -> str:
    return f"/event/{quote(row.slug or row.id, safe='')}"


def _cover(cfg: Settings, row: EventRow) -> str | None:
    if row.cover_url:
        return cfg.site.base_url + row.cover_url
    return row.image_url


def _when(row: EventRow, cfg: Settings, lang: str) -> str:
    if row.start_at is None:
        return ""
    local = row.start_at.astimezone(ZoneInfo(cfg.timezone))
    return f"{DAYS[lang][local.weekday()]} {local:%d/%m/%Y %H:%M}"


def _price(row: EventRow, lang: str) -> str:
    if row.entry_type == "free" or row.min_price == 0:
        return TEXT[lang]["free"]
    if row.min_price is None:
        return ""
    symbol = CURRENCY_SYMBOLS.get(row.currency, f"{row.currency} ")
    return TEXT[lang]["from"].format(p=f"{symbol}{float(row.min_price):g}")


def _summary(row: EventRow, cfg: Settings, lang: str) -> str:
    parts = [_when(row, cfg, lang), row.venue_name or row.city, _price(row, lang)]
    if row.lineup:
        parts.append(", ".join(row.lineup[:5]))
    return " · ".join(p for p in parts if p)


def _clip(text: str | None, limit: int = DESC_LIMIT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iso(dt: datetime | None, cfg: Settings) -> str | None:
    return dt.astimezone(ZoneInfo(cfg.timezone)).isoformat() if dt else None


def _upcoming(session: Session, *where: Any, limit: int = LIST_LIMIT) -> list[EventRow]:
    return list(
        session.scalars(
            select(EventRow)
            .where(upcoming_filter(), EventRow.country.is_(None), *where)
            .order_by(EventRow.start_at)
            .limit(limit)
            .options(selectinload(EventRow.images))
        )
    )


def _event_list_html(rows: list[EventRow], cfg: Settings, lang: str) -> str:
    items = "".join(
        f'<li><a href="{escape(event_path(r))}">{escape(r.title)}</a> · {escape(_summary(r, cfg, lang))}</li>'
        for r in rows
    )
    return f"<ul>{items}</ul>"


# --------------------------------------------------------------------------- JSON-LD
def _place(row: EventRow) -> dict[str, Any]:
    place: dict[str, Any] = {
        "@type": "Place",
        "name": row.venue_name or row.address or row.city or "Israel",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": row.address or None,
            "addressLocality": row.city or None,
            "addressCountry": "IL",
        },
    }
    if row.latitude is not None and row.longitude is not None:
        place["geo"] = {"@type": "GeoCoordinates", "latitude": row.latitude, "longitude": row.longitude}
    return place


def _offers(row: EventRow, cfg: Settings) -> list[dict[str, Any]] | dict[str, Any] | None:
    availability = {
        "on_sale": "https://schema.org/InStock",
        "sold_out": "https://schema.org/SoldOut",
        "coming_soon": "https://schema.org/PreOrder",
        "closed": "https://schema.org/Discontinued",
    }
    offers = [
        {
            "@type": "Offer",
            "name": t.name,
            "price": float(t.total_price if t.total_price is not None else t.price or 0),
            "priceCurrency": t.currency,
            "availability": availability.get(t.status, "https://schema.org/InStock"),
            "url": row.url,
            "validFrom": _iso(t.sale_start, cfg),
        }
        for t in row.tickets
        if t.price is not None or t.is_free
    ]
    if offers:
        return offers
    if row.min_price is not None:
        return {
            "@type": "AggregateOffer",
            "lowPrice": float(row.min_price),
            "highPrice": float(row.max_price if row.max_price is not None else row.min_price),
            "priceCurrency": row.currency,
            "url": row.url,
            "availability": "https://schema.org/SoldOut" if row.is_sold_out else "https://schema.org/InStock",
        }
    return None


def event_jsonld(row: EventRow, cfg: Settings) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "MusicEvent" if row.lineup or row.genres else "Event",
        "@id": _url(cfg, event_path(row)) + "#event",
        "name": row.title,
        "url": _url(cfg, event_path(row)),
        "startDate": _iso(row.start_at, cfg),
        "endDate": _iso(row.end_at, cfg),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": _place(row),
        "image": [i for i in [_cover(cfg, row)] if i],
        "description": _clip(row.description) or None,
        "inLanguage": "he",
        "isAccessibleForFree": row.entry_type == "free" or None,
        "typicalAgeRange": f"{row.min_age}-" if row.min_age else None,
        "keywords": ", ".join(row.genres) or None,
        "performer": [{"@type": "Person", "name": n, "url": _url(cfg, f"/artist/{quote(n, safe='')}")} for n in row.lineup] or None,
        "organizer": {"@type": "Organization", "name": row.organizer} if row.organizer else None,
        "offers": _offers(row, cfg),
        "sameAs": row.url,
    }
    return {k: v for k, v in data.items() if v not in (None, [], "")}


def _breadcrumbs(cfg: Settings, lang: str, *crumbs: tuple[str, str]) -> dict[str, Any]:
    items = [(cfg.site.name, "/"), *crumbs]
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": _url(cfg, path, lang)}
            for i, (name, path) in enumerate(items, start=1)
        ],
    }


def _item_list(rows: list[EventRow], cfg: Settings, name: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "itemListElement": [
            {"@type": "ListItem", "position": i, "url": _url(cfg, event_path(r)), "name": r.title}
            for i, r in enumerate(rows, start=1)
        ],
    }


def _website(cfg: Settings, lang: str) -> list[dict[str, Any]]:
    org = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": cfg.site.base_url + "/#org",
        "name": cfg.site.name,
        "url": cfg.site.base_url + "/",
        "logo": cfg.site.base_url + "/icon-512.png",
        "areaServed": {"@type": "Country", "name": "Israel"},
    }
    site = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": cfg.site.base_url + "/#website",
        "name": cfg.site.name,
        "url": cfg.site.base_url + "/",
        "description": TEXT[lang]["site_desc"],
        "inLanguage": ["he", "en"],
        "publisher": {"@id": cfg.site.base_url + "/#org"},
    }
    return [org, site]


# --------------------------------------------------------------------------- pages
def home_page(session: Session, cfg: Settings, lang: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session)
    return Page(
        title=t["site_title"],
        description=t["site_desc"],
        path="/",
        lang=lang,
        jsonld=[*_website(cfg, lang), _item_list(rows, cfg, t["upcoming"])],
        body=f"<h1>{escape(t['site_title'])}</h1><p>{escape(t['site_desc'])}</p>{_landing_links(session, cfg, lang)}"
        f"<h2>{escape(t['upcoming'])}</h2>" + _event_list_html(rows, cfg, lang),
    )


def event_page(session: Session, cfg: Settings, lang: str, ref: str) -> Page | None:
    row = session.scalar(
        select(EventRow)
        .where(or_(EventRow.id == ref, EventRow.slug == ref))
        .options(selectinload(EventRow.tickets), selectinload(EventRow.images))
    )
    if row is None:
        return None
    t = TEXT[lang]
    summary = _summary(row, cfg, lang)
    where = " · ".join(x for x in [row.venue_name, row.address, row.city] if x)
    lineup = (
        f"<h2>{t['lineup']}</h2><ul>"
        + "".join(f'<li><a href="/artist/{quote(n, safe="")}">{escape(n)}</a></li>' for n in row.lineup)
        + "</ul>"
        if row.lineup
        else ""
    )
    venue_link = f'<a href="/venue/{quote(row.venue_name, safe="")}">{escape(row.venue_name)}</a>' if row.venue_name else ""
    crumbs = [(row.venue_name, f"/venue/{quote(row.venue_name, safe='')}")] if row.venue_name else []
    return Page(
        title=f"{row.title} · {_when(row, cfg, lang)} · {cfg.site.name}",
        description=_clip(f"{summary}. {row.description or ''}", 160),
        path=event_path(row),
        lang=lang,
        image=_cover(cfg, row),
        og_type="event",
        jsonld=[event_jsonld(row, cfg), _breadcrumbs(cfg, lang, *crumbs, (row.title, event_path(row)))],
        body=(
            f"<article><h1>{escape(row.title)}</h1><p>{escape(_when(row, cfg, lang))}</p>"
            f"<p>{venue_link} {escape(where)}</p><p>{escape(_price(row, lang))}</p>{lineup}"
            f"<p>{escape(_clip(row.description, 2000))}</p>"
            f'<p><a href="{escape(row.url)}" rel="noopener">{t["tickets"]} ({escape(row.source)})</a></p></article>'
        ),
    )


def venue_page(session: Session, cfg: Settings, lang: str, name: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session, EventRow.venue_name.ilike(f"%{name}%"))
    path = f"/venue/{quote(name, safe='')}"
    place: dict[str, Any] = {"@context": "https://schema.org", **(_place(rows[0]) if rows else {"@type": "Place"}), "name": name}
    place["@type"] = "MusicVenue"
    place["url"] = _url(cfg, path)
    if rows:
        place["event"] = [{"@id": _url(cfg, event_path(r)) + "#event", "name": r.title, "startDate": _iso(r.start_at, cfg)} for r in rows]
    return Page(
        title=f"{name} · {t['upcoming']} · {cfg.site.name}",
        description=t["venue_desc"].format(name=name),
        path=path,
        lang=lang,
        image=_cover(cfg, rows[0]) if rows else None,
        jsonld=[place, _breadcrumbs(cfg, lang, (t["venues"], "/venues"), (name, path))],
        body=f"<h1>{escape(name)}</h1><p>{escape(t['venue_desc'].format(name=name))}</p>" + _event_list_html(rows, cfg, lang),
        noindex=not rows,
    )


def artist_page(session: Session, cfg: Settings, lang: str, name: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session, cast(EventRow.lineup, String).ilike(f"%{name}%"))
    path = f"/artist/{quote(name, safe='')}"
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "url": _url(cfg, path),
        "performerIn": [{"@id": _url(cfg, event_path(r)) + "#event", "name": r.title, "startDate": _iso(r.start_at, cfg)} for r in rows],
    }
    return Page(
        title=f"{name} · {t['artists']} · {cfg.site.name}",
        description=t["artist_desc"].format(name=name),
        path=path,
        lang=lang,
        image=_cover(cfg, rows[0]) if rows else None,
        jsonld=[person, _breadcrumbs(cfg, lang, (t["artists"], "/artists"), (name, path))],
        body=f"<h1>{escape(name)}</h1><p>{escape(t['artist_desc'].format(name=name))}</p>" + _event_list_html(rows, cfg, lang),
        noindex=not rows,
    )


def _names(session: Session, kind: str) -> list[str]:
    rows = session.scalars(select(EventRow).where(upcoming_filter())).all()
    counts: dict[str, int] = {}
    for r in rows:
        for n in ([r.venue_name] if kind == "venues" else r.lineup or []):
            if n:
                counts[n] = counts.get(n, 0) + 1
    return sorted(counts, key=lambda n: (-counts[n], n))


def index_page(session: Session, cfg: Settings, lang: str, kind: str) -> Page:
    t = TEXT[lang]
    names = _names(session, kind)[:300]
    prefix = "/venue/" if kind == "venues" else "/artist/"
    links = "".join(f'<li><a href="{prefix}{quote(n, safe="")}">{escape(n)}</a></li>' for n in names)
    return Page(
        title=f"{t[kind]} · {cfg.site.name}",
        description=t["site_desc"],
        path=f"/{kind}",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (t[kind], f"/{kind}"))],
        body=f"<h1>{escape(t[kind])}</h1><ul>{links}</ul>",
    )


def map_page(session: Session, cfg: Settings, lang: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session, EventRow.latitude.is_not(None), limit=200)
    return Page(
        title=f"{t['map']} · {cfg.site.name}",
        description=t["site_desc"],
        path="/map",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (t["map"], "/map"))],
        body=f"<h1>{escape(t['map'])}</h1>" + _event_list_html(rows, cfg, lang),
    )


def abroad_page(session: Session, cfg: Settings, lang: str) -> Page:
    t = TEXT[lang]
    rows = list(
        session.scalars(
            select(EventRow)
            .where(upcoming_filter(), EventRow.country.is_not(None), EventRow.is_primary.is_(True))
            .order_by(EventRow.start_at)
            .limit(LIST_LIMIT)
            .options(selectinload(EventRow.images))
        )
    )
    return Page(
        title=f"{t['abroad']} · {cfg.site.name}",
        description=t["abroad_desc"],
        path="/abroad",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (t["abroad"], "/abroad"))],
        body=f"<h1>{escape(t['abroad'])}</h1>" + _event_list_html(rows, cfg, lang),
        noindex=not rows,
    )


def accessibility_page(session: Session, cfg: Settings, lang: str) -> Page:
    a = cfg.site.accessibility
    he = lang == "he"
    title = "הצהרת נגישות" if he else "Accessibility statement"
    text = (
        f"{cfg.site.name} פועל להנגשת האתר לכלל הגולשים, בהתאם לתקנות שוויון זכויות לאנשים עם מוגבלות "
        "(התאמות נגישות לשירות), התשע\"ג-2013, ולתקן הישראלי ת\"י 5568 (WCAG 2.0 ברמה AA)."
        if he
        else f"{cfg.site.name} works to make this website accessible to everyone, per the Israeli Equal Rights for "
        "Persons with Disabilities regulations (2013) and Israeli Standard IS 5568 (WCAG 2.0 level AA)."
    )
    contact = ""
    if a.has_contact:
        items = [a.coordinator, a.email, a.phone]
        contact = "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items if i) + "</ul>"
    return Page(
        title=f"{title} · {cfg.site.name}",
        description=text,
        path="/accessibility",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (title, "/accessibility"))],
        body=f"<h1>{title}</h1><p>{escape(text)}</p>{contact}",
    )


def privacy_page(session: Session, cfg: Settings, lang: str) -> Page:
    he = lang == "he"
    title = "מדיניות פרטיות ועוגיות" if he else "Privacy and cookie policy"
    text = (
        f"{cfg.site.name} פועל לפי חוק הגנת הפרטיות, התשמ\"א-1981 (כולל תיקון 13). אין באתר הרשמה, "
        "עוגיות פרסום, מעקב או סטטיסטיקה; נשמרים בדפדפן רק פריטים הכרחיים, ותוכן מצד שלישי (מפה) נטען רק בהסכמה."
        if he
        else f"{cfg.site.name} follows the Israeli Privacy Protection Law, 1981 (including Amendment 13). There is no "
        "sign-up and no advertising, tracking or analytics cookies; only necessary items are stored in the browser, "
        "and third-party content (the map) loads only with consent."
    )
    contact = f"<p>{escape(cfg.site.privacy.email)}</p>" if cfg.site.privacy.email else ""
    return Page(
        title=f"{title} · {cfg.site.name}",
        description=text,
        path="/privacy",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (title, "/privacy"))],
        body=f"<h1>{title}</h1><p>{escape(text)}</p>{contact}",
    )


def terms_page(session: Session, cfg: Settings, lang: str) -> Page:
    he = lang == "he"
    title = "תנאי שימוש" if he else "Terms of use"
    text = (
        f"{cfg.site.name} רק אוסף ומציג אירועים שנמכרים באתרים אחרים, ואינו מוכר כרטיסים ואינו צד לעסקה. האתר "
        "ובעליו אינם אחראים לאירועים, לתוכנם, למחירים או לשינויי מחיר; הרכישה באחריות אתר הכרטיסים והמארגן."
        if he
        else f"{cfg.site.name} only collects and shows events sold on other sites; it sells no tickets and is not a "
        "party to any purchase. The site and its owners are not responsible for the events, their content, prices "
        "or price changes; purchases are the responsibility of the ticketing site and the organizer."
    )
    return Page(
        title=f"{title} · {cfg.site.name}",
        description=text,
        path="/terms",
        lang=lang,
        jsonld=[_breadcrumbs(cfg, lang, (title, "/terms"))],
        body=f"<h1>{title}</h1><p>{escape(text)}</p>",
    )


# --------------------------------------------------------------------------- landing pages
# kind: "tonight" | "weekend" | "genre" | "city", or "genre_tonight" / "genre_weekend" for one genre
# tonight or this weekend ("טכנו הערב", "סטנדאפ בסופ״ש"). Every page needs GENRE_PAGE_MIN events.
WHENS = ("tonight", "weekend")


def _genre_filter(genre: str, cfg: Settings) -> ColumnElement[bool]:
    # An umbrella genre ("electronic") also matches its sub-genres (techno, house...).
    return or_(*(cast(EventRow.genres, String).like(f'%"{g}"%') for g in cfg.enrichment.expand_genres([genre])))


def _when_where(when: str | None, cfg: Settings) -> list[ColumnElement[bool]]:
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    if when == "tonight":
        return [EventRow.local_date == today]
    if when == "weekend":
        return [EventRow.is_weekend.is_(True), EventRow.local_date <= today + timedelta(days=7)]
    return []


def genre_counts(session: Session, cfg: Settings | None = None, when: str | None = None) -> dict[str, int]:
    """Genres with enough upcoming events (tonight / this weekend) for a landing page, most events first."""
    counts: Counter[str] = Counter()
    where = _when_where(when, cfg) if cfg else []
    for genres in session.scalars(select(EventRow.genres).where(upcoming_filter(), EventRow.country.is_(None), *where)):
        counts.update(cfg.enrichment.with_parents(genres or []) if cfg else genres or [])
    return {g: n for g, n in counts.most_common() if n >= GENRE_PAGE_MIN}


def city_pages(session: Session, cfg: Settings) -> list[CitySettings]:
    """Configured cities with enough upcoming events for a page."""
    counts = Counter(session.scalars(select(EventRow.city).where(upcoming_filter())))
    return [c for c in cfg.enrichment.cities if counts[c.he] >= GENRE_PAGE_MIN]


def _landing_paths(session: Session, cfg: Settings) -> list[tuple[str, str, str]]:
    """(path, Hebrew name, English name) of every landing page that currently has enough events."""
    out = [("/tonight", "מה עושים הערב", "Tonight"), ("/weekend", "סוף השבוע", "This weekend")]
    out += [(f"/city/{c.key}", c.he, c.en) for c in city_pages(session, cfg)]
    out += [(f"/genre/{g}", genre_label(g, "he"), genre_label(g, "en")) for g in genre_counts(session, cfg)]
    for when, he, en in (("tonight", "הערב", "tonight"), ("weekend", "בסופ״ש", "this weekend")):
        out += [(f"/genre/{g}/{when}", f"{genre_label(g, 'he')} {he}", f"{genre_label(g, 'en')} {en}")
                for g in genre_counts(session, cfg, when)]  # fmt: skip
    return out


def _landing_text(kind: str, lang: str, label: str) -> tuple[str, str, str]:
    """(h1 / title, meta description, intro paragraph). ``label`` is the genre or city name."""
    g = label
    if lang == "he":
        return {
            "tonight": ("מה עושים הערב? כל האירועים הערב בישראל",
                        "מה עושים הערב: כל המסיבות, ההופעות, הסטנדאפ וההצגות של הערב בישראל, עם שעות, מחירים, כניסה חופשית וכרטיסים.",
                        "כל האירועים שמתקיימים הערב, מעודכן כמה פעמים ביום מאתרי הכרטיסים בישראל."),
            "weekend": ("מה עושים בסוף השבוע? אירועים ומסיבות בסופ״ש",
                        "מסיבות, הופעות ואירועים לסוף השבוע הקרוב בישראל: חמישי, שישי ושבת, עם מחירים וכרטיסים.",
                        "כל האירועים של סוף השבוע הקרוב, מחמישי ועד מוצאי שבת."),
            "genre": (f"{g}: אירועים ומסיבות קרובים בישראל",
                      f"כל אירועי ה{g} הקרובים בישראל: תאריכים, ליינאפ, מקומות, מחירי כרטיסים וכניסה חופשית.",
                      f"כל אירועי ה{g} הקרובים, מכל אתרי הכרטיסים במקום אחד."),
            "genre_tonight": (f"{g} הערב: כל אירועי ה{g} של הערב",
                              f"{g} הערב בישראל: כל אירועי ה{g} שמתקיימים הערב, עם שעות, מקומות, מחירים וכרטיסים.",
                              f"כל אירועי ה{g} של הערב, מעודכן כמה פעמים ביום."),
            "genre_weekend": (f"{g} בסופ״ש: אירועי {g} בסוף השבוע הקרוב",
                              f"{g} בסוף השבוע: כל אירועי ה{g} של חמישי, שישי ושבת הקרובים בישראל, עם מחירים וכרטיסים.",
                              f"כל אירועי ה{g} של סוף השבוע הקרוב, מחמישי ועד מוצאי שבת."),
            "city": (f"מה עושים ב{g}? אירועים ומסיבות ב{g}",
                     f"כל האירועים, המסיבות וההופעות הקרובים ב{g}: תאריכים, ליינאפ, מקומות, מחירי כרטיסים וכניסה חופשית.",
                     f"כל האירועים הקרובים ב{g}, מכל אתרי הכרטיסים במקום אחד."),
        }[kind]  # fmt: skip
    return {
        "tonight": ("What's on tonight in Israel",
                    "Everything happening tonight in Israel: parties, live music, stand-up and shows, with times, prices, free entry and tickets.",
                    "Every event happening tonight, refreshed several times a day from Israeli ticketing sites."),
        "weekend": ("What's on this weekend in Israel",
                    "Parties, concerts and events for the coming weekend in Israel (Thursday to Saturday), with prices and tickets.",
                    "Every event this coming weekend, Thursday through Saturday night."),
        "genre": (f"{g} events in Israel",
                  f"Upcoming {g} events in Israel: dates, lineups, venues, ticket prices and free entry.",
                  f"Every upcoming {g} event, from every ticketing site in one place."),
        "genre_tonight": (f"{g} tonight in Israel",
                          f"{g} events happening tonight in Israel, with times, venues, prices and tickets.",
                          f"Every {g} event tonight, refreshed several times a day."),
        "genre_weekend": (f"{g} this weekend in Israel",
                          f"{g} events this coming weekend in Israel (Thursday to Saturday), with prices and tickets.",
                          f"Every {g} event this coming weekend, Thursday through Saturday night."),
        "city": (f"What's on in {g}: events and parties",
                 f"Upcoming events, parties and shows in {g}: dates, lineups, venues, ticket prices and free entry.",
                 f"Every upcoming event in {g}, from every ticketing site in one place."),
    }[kind]  # fmt: skip


def _faq(rows: list[EventRow], kind: str, lang: str, label: str, site: str) -> list[tuple[str, str]]:
    """Questions people ask, answered from the live listings (what AI answer engines quote)."""
    he = lang == "he"
    scope = {"tonight": ("הערב", "tonight"), "weekend": ("בסוף השבוע", "this weekend"),
             "genre": (f"בז׳אנר {label}", f"for {label}"), "city": (f"ב{label}", f"in {label}"),
             "genre_tonight": (f"בז׳אנר {label} הערב", f"for {label} tonight"),
             "genre_weekend": (f"בז׳אנר {label} בסוף השבוע", f"for {label} this weekend")}[kind][0 if he else 1]  # fmt: skip
    free = [r for r in rows if r.entry_type == "free" or r.min_price == 0]
    priced = sorted((r for r in rows if r.min_price), key=lambda r: r.min_price or 0)
    venues = [v for v, _ in Counter(r.venue_name for r in rows if r.venue_name).most_common(3)]
    qa = [(
        f"כמה אירועים יש {scope}?" if he else f"How many events are on {scope}?",
        f"כרגע יש {len(rows)} אירועים {scope} ב-{site}." if he else f"There are currently {len(rows)} events {scope} on {site}.",
    )]  # fmt: skip
    if free:
        names = ", ".join(r.title for r in free[:3])
        qa.append((
            f"יש אירועים בכניסה חופשית {scope}?" if he else f"Are there free events {scope}?",
            f"כן, {len(free)} אירועים בכניסה חופשית, למשל: {names}." if he else f"Yes, {len(free)} free events, such as {names}.",
        ))  # fmt: skip
    if priced:
        r, price = priced[0], f"{float(priced[0].min_price or 0):g}"
        at = f" ב{r.venue_name}" if he and r.venue_name else f" at {r.venue_name}" if r.venue_name else ""
        qa.append((
            f"מה הכרטיס הכי זול {scope}?" if he else f"What is the cheapest ticket {scope}?",
            f"{r.title}{at}, החל מ־₪{price}." if he else f"{r.title}{at}, from ₪{price}.",
        ))  # fmt: skip
    if venues:
        qa.append((f"איפה יש הכי הרבה אירועים {scope}?" if he else f"Which venues have the most events {scope}?", ", ".join(venues) + "."))
    return qa


def landing_page(
    session: Session, cfg: Settings, lang: str, kind: str, genre: str | None = None, city: str | None = None
) -> Page | None:
    when = kind.removeprefix("genre_") if kind.startswith("genre_") else kind if kind in WHENS else None
    where = _when_where(when, cfg)
    if kind.startswith("genre"):
        if genre not in genre_counts(session, cfg, when):
            return None
        where.append(_genre_filter(genre or "", cfg))
        label, path = genre_label(genre or "", lang), f"/genre/{genre}" + (f"/{when}" if when else "")
    elif kind == "city":
        found = next((c for c in city_pages(session, cfg) if c.key == city), None)
        if found is None:
            return None
        where.append(EventRow.city == found.he)
        label, path = (found.he if lang == "he" else found.en), f"/city/{found.key}"
    else:
        label, path = "", f"/{kind}"
    rows = _upcoming(session, *where, limit=500)
    title, desc, intro = _landing_text(kind, lang, label)
    faq = _faq(rows, kind, lang, label, cfg.site.name)
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
    }
    faq_html = "".join(f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in faq)
    return Page(
        title=f"{title} · {cfg.site.name}",
        description=desc,
        path=path,
        lang=lang,
        image=_cover(cfg, rows[0]) if rows else None,
        jsonld=[_item_list(rows[:LIST_LIMIT], cfg, title), faq_ld, _breadcrumbs(cfg, lang, (title, path))],
        body=f"<h1>{escape(title)}</h1><p>{escape(intro)}</p>" + _event_list_html(rows[:LIST_LIMIT], cfg, lang)
        + f"<section><h2>{'שאלות נפוצות' if lang == 'he' else 'FAQ'}</h2>{faq_html}</section>",
    )


def _landing_links(session: Session, cfg: Settings, lang: str) -> str:
    """Links from the home page, so crawlers reach every landing page."""
    links = [(p, he if lang == "he" else en) for p, he, en in [*_landing_paths(session, cfg), ("/blog", "המגזין", "Magazine")]]
    return "<nav><ul>" + "".join(f'<li><a href="{p}">{escape(n)}</a></li>' for p, n in links) + "</ul></nav>"


# --------------------------------------------------------------------------- render
def _jsonld_tag(data: dict[str, Any]) -> str:
    # "</" inside a string would close the script element early.
    text = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return f'<script type="application/ld+json">{text}</script>'


def render(template: str, page: Page, cfg: Settings) -> str:
    canonical = _url(cfg, page.path, page.lang if page.lang in page.langs else page.langs[0])
    image = page.image or f"{cfg.site.base_url}/og-default.png"
    t = TEXT[page.lang]
    other = "en" if page.lang == "he" else "he"
    head = [
        f"<title>{escape(page.title)}</title>",
        f'<meta name="description" content="{escape(page.description)}" />',
        f'<link rel="canonical" href="{escape(canonical)}" />',
        *(f'<link rel="alternate" hreflang="{code}" href="{escape(_url(cfg, page.path, code))}" />'
          for code in page.langs if len(page.langs) > 1),
        *([f'<link rel="alternate" hreflang="x-default" href="{escape(_url(cfg, page.path, "he"))}" />'] if len(page.langs) > 1 else []),
        '<meta name="robots" content="noindex, nofollow" />' if page.noindex
        else '<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1" />',
        f'<meta property="og:type" content="{page.og_type}" />',
        f'<meta property="og:site_name" content="{escape(cfg.site.name)}" />',
        f'<meta property="og:title" content="{escape(page.title)}" />',
        f'<meta property="og:description" content="{escape(page.description)}" />',
        f'<meta property="og:url" content="{escape(canonical)}" />',
        f'<meta property="og:image" content="{escape(image)}" />',
        f'<meta property="og:locale" content="{t["locale"]}" />',
        *([f'<meta property="og:locale:alternate" content="{TEXT[other]["locale"]}" />'] if len(page.langs) > 1 else []),
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{escape(page.title)}" />',
        f'<meta name="twitter:description" content="{escape(page.description)}" />',
        f'<meta name="twitter:image" content="{escape(image)}" />',
        # Local signals: the whole site is about Israel.
        '<meta name="geo.region" content="IL" />',
        '<meta name="geo.placename" content="Israel" />',
        '<link rel="alternate" type="text/plain" title="llms.txt" href="/llms.txt" />',
        f'<link rel="alternate" type="application/rss+xml" title="{escape(cfg.site.name)}" href="/blog/rss.xml" />',
        *(_jsonld_tag(d) for d in page.jsonld),
    ]
    html = template.replace("<!--seo-->", "\n    ".join(head), 1)
    html = _strip_title(html, keep=head[0])
    html = html.replace('<html lang="he" dir="rtl">', f'<html lang="{page.lang}" dir="{"rtl" if page.lang == "he" else "ltr"}">', 1)
    return html.replace('<div id="root"></div>', f'<div id="root"><div class="ssr">{page.body}</div></div>', 1)


def _strip_title(html: str, keep: str) -> str:
    """Drop the template's static <title>, keeping the one injected for this page."""
    start = html.find("<title>")
    end = html.find("</title>", start)
    if start != -1 and html[start : end + 8] != keep:
        html = html[:start] + html[end + 8 :].lstrip()
    return html


# --------------------------------------------------------------------------- crawl files
def sitemap(session: Session, cfg: Settings) -> str:
    rows = session.scalars(select(EventRow).where(upcoming_filter()).order_by(EventRow.start_at)).all()
    paths: list[tuple[str, date | None]] = [("/", None), ("/map", None), ("/abroad", None), ("/venues", None), ("/artists", None), ("/accessibility", None), ("/privacy", None), ("/terms", None)]
    today = datetime.now(ZoneInfo(cfg.timezone))  # landing pages change every day
    paths += [(p, today) for p, _, _ in _landing_paths(session, cfg)]
    paths += [(event_path(r), r.last_seen_at) for r in rows]
    paths += [(f"/venue/{quote(n, safe='')}", None) for n in _names(session, "venues")]
    paths += [(f"/artist/{quote(n, safe='')}", None) for n in _names(session, "artists")]
    urls = []
    for path, modified in paths:
        he, en = escape(_url(cfg, path, "he")), escape(_url(cfg, path, "en"))
        lastmod = f"<lastmod>{modified:%Y-%m-%d}</lastmod>" if modified else ""
        alternates = (
            f'<xhtml:link rel="alternate" hreflang="he" href="{he}"/>'
            f'<xhtml:link rel="alternate" hreflang="en" href="{en}"/>'
            f'<xhtml:link rel="alternate" hreflang="x-default" href="{he}"/>'
        )
        urls += [f"<url><loc>{u}</loc>{lastmod}{alternates}</url>" for u in (he, en)]
    # The blog is Hebrew only: one URL each, no alternates.
    from partylinks import blog

    posts = blog.load_posts(cfg)
    blog_paths = [("/blog", posts[0].modified if posts else None)]
    blog_paths += [(f"/blog/category/{k}", None) for k in sorted({p.category for p in posts})]
    blog_paths += [(f"/blog/{p.slug}", p.modified) for p in posts]
    for path, modified in blog_paths:
        lastmod = f"<lastmod>{modified:%Y-%m-%d}</lastmod>" if modified else ""
        urls.append(f"<url><loc>{escape(_url(cfg, path))}</loc>{lastmod}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def robots(cfg: Settings) -> str:
    private = "".join(f"Disallow: {p}\n" for p in ROBOTS_PRIVATE)
    ai = "".join(f"User-agent: {bot}\n" for bot in AI_CRAWLERS)
    blocked = "".join(f"User-agent: {bot}\n" for bot in cfg.security.blocked_bots)
    return (
        f"# Search engines and AI assistants are welcome: the event listings are public.\n"
        f"User-agent: *\nAllow: /\n{private}\n{ai}Allow: /\n{private}\n"
        + (f"# Data harvesters: not welcome (also refused by the server).\n{blocked}Disallow: /\n\n" if blocked else "")
        + f"Sitemap: {cfg.site.base_url}/sitemap.xml\n"
    )


def llms_txt(session: Session, cfg: Settings) -> str:
    """https://llmstxt.org — a plain summary of the site and its live listings for AI assistants."""
    from partylinks import blog

    base = cfg.site.base_url
    rows = _upcoming(session, limit=LLMS_LIMIT)
    now = datetime.now(ZoneInfo(cfg.timezone))
    lines = [
        f"# {cfg.site.name}",
        "",
        "> Every event, party, show and club night in Israel, collected from Israeli "
        "ticketing sites and updated several times a day. For each event: date and time (Israel time, "
        "Asia/Jerusalem), venue and address, lineup / DJs, genres, minimum age, every ticket tier with "
        "its price in ILS (₪), free-entry flags, and a link to buy tickets on the original site.",
        "",
        f"Last updated: {now:%Y-%m-%d %H:%M} (Asia/Jerusalem). Site languages: Hebrew (default) and English (`?lang=en`).",
        "",
        "## Pages",
        f"- [Upcoming events]({base}/): all upcoming events, filterable by date, genre, time of day and free entry",
        f"- [Map]({base}/map): events with a location",
        f"- [Venues]({base}/venues): every venue with upcoming events",
        f"- [Artists]({base}/artists): DJs and performers with upcoming shows",
        *(f"- [{en}]({base}{p}?lang=en): upcoming events, with an FAQ (free events, cheapest ticket)"
          for p, _, en in _landing_paths(session, cfg)),
        "",
        "## Magazine (Hebrew guides, with live event lists)",
        *(f"- [{p.title}]({base}/blog/{p.slug}): {p.description}" for p in blog.load_posts(cfg)),
        "",
        "## Data (JSON API, no key needed)",
        f"- [Events]({base}/events): filters `date=YYYY-MM-DD`, `weekend=true`, `genre`, `artist`, `venue`, "
        "`city`, `free_entry=true`, `time_slot` (morning/afternoon/evening/night), `area` (south, rothschild, center, business, "
        "beach, north, jaffa, outside), `min_price`, `max_price`, `q`; paged with `page`, `page_size`",
        f"- [Event detail]({base}/events/{{id}}): tickets, prices, fees, images",
        f"- [Magazine posts]({base}/posts): blog posts as JSON; `/posts/{{slug}}` for one post",
        f"- [Categories]({base}/categories): counts per genre, date, venue, artist",
        *([f"- [OpenAPI schema]({base}/openapi.json)"] if cfg.security.api_docs else []),
        "",
        f"## Upcoming events (next {len(rows)})",
    ]
    for r in rows:
        genres = f" · {', '.join(r.genres)}" if r.genres else ""
        lines.append(f"- [{r.title}]({_url(cfg, event_path(r))}): {_summary(r, cfg, 'en')}{genres}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- routes
def register(app: FastAPI, site_dir: Path) -> None:
    """Crawl files always; server-rendered pages only when the React build exists."""

    def cfg(request: Request) -> Settings:
        return request.app.state.settings

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt(request: Request) -> PlainTextResponse:
        return PlainTextResponse(robots(cfg(request)))

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml(request: Request) -> Response:
        with request.app.state.db.session() as session:
            return Response(sitemap(session, cfg(request)), media_type="application/xml")

    @app.get("/llms.txt", include_in_schema=False)
    def llms(request: Request) -> PlainTextResponse:
        with request.app.state.db.session() as session:
            return PlainTextResponse(llms_txt(session, cfg(request)), media_type="text/markdown; charset=utf-8")

    from partylinks import blog

    index = site_dir / "index.html"

    def page(request: Request, build: Any) -> HTMLResponse:
        lang = "en" if request.query_params.get("lang") == "en" else "he"
        with request.app.state.db.session() as session:
            result = build(session, cfg(request), lang)
        if result is None:
            t = TEXT[lang]
            result = Page(t["not_found"], t["site_desc"], request.url.path, lang, noindex=True, status=404,
                          body=f"<h1>{t['not_found']}</h1>")  # fmt: skip
        html = render(index.read_text(encoding="utf-8"), result, cfg(request))
        return HTMLResponse(html, status_code=result.status)

    blog.register(app, page if index.is_file() else None)
    if not index.is_file():
        return

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> HTMLResponse:
        return page(request, home_page)

    @app.get("/event/{ref}", include_in_schema=False)
    def event(ref: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: event_page(s, c, lang, ref))

    @app.get("/venue/{name}", include_in_schema=False)
    def venue(name: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: venue_page(s, c, lang, name))

    @app.get("/artist/{name}", include_in_schema=False)
    def artist(name: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: artist_page(s, c, lang, name))

    @app.get("/venues", include_in_schema=False)
    def venues(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: index_page(s, c, lang, "venues"))

    @app.get("/artists", include_in_schema=False)
    def artists(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: index_page(s, c, lang, "artists"))

    @app.get("/accessibility", include_in_schema=False)
    def accessibility(request: Request) -> HTMLResponse:
        return page(request, accessibility_page)

    @app.get("/privacy", include_in_schema=False)
    def privacy(request: Request) -> HTMLResponse:
        return page(request, privacy_page)

    @app.get("/terms", include_in_schema=False)
    def terms(request: Request) -> HTMLResponse:
        return page(request, terms_page)

    @app.get("/map", include_in_schema=False)
    def map_(request: Request) -> HTMLResponse:
        return page(request, map_page)

    @app.get("/abroad", include_in_schema=False)
    def abroad(request: Request) -> HTMLResponse:
        return page(request, abroad_page)

    @app.get("/tonight", include_in_schema=False)
    def tonight(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, "tonight"))

    @app.get("/weekend", include_in_schema=False)
    def weekend(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, "weekend"))

    @app.get("/genre/{genre}", include_in_schema=False)
    def genre(genre: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, "genre", genre))

    @app.get("/genre/{genre}/{when}", include_in_schema=False)
    def genre_when(genre: str, when: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, f"genre_{when}", genre) if when in WHENS else None)

    @app.get("/city/{city}", include_in_schema=False)
    def city(city: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, "city", city=city))

    @app.get("/admin", include_in_schema=False)
    def admin(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: Page("Admin", "", "/admin", lang, noindex=True))
