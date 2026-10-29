"""Search, social and AI visibility for the React site.

The site renders in the browser, but crawlers, link previews and LLM fetchers often don't run
JavaScript. So every public page is served from here with its own ``<head>`` (title, description,
canonical, hreflang, Open Graph / Twitter, local geo tags), schema.org JSON-LD, and a plain-HTML
copy of the content inside ``#root`` that React replaces on load. Also serves ``sitemap.xml``,
``robots.txt`` and ``llms.txt``.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import yaml
from cachetools import TTLCache
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import ColumnElement, String, cast, or_, select
from sqlalchemy.orm import Session, selectinload

from partylinks.config import CitySettings, Settings, get_settings
from partylinks.countries import country_name, is_country
from partylinks.db import EventRow, lineup_contains, upcoming_filter
from partylinks.places import all_cities

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"
LIST_LIMIT = 60  # events listed on index pages
HTML_TTL_S = 300  # a rendered page is reused this long
HTML_CACHE_BYTES = 64 << 20
LLMS_LIMIT = 400
DESC_LIMIT = 300
AI_CRAWLERS = [
    "OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Perplexity-User", "Bingbot",
]  # fmt: skip

# Operational / private endpoints crawlers have no business indexing.
ROBOTS_PRIVATE = ["/admin", "/admin/api/", "/docs", "/redoc", "/openapi.json", "/health", "/runs", "/sources",
                  "/maintenance", "/changes", "/go/"]

CURRENCY_SYMBOLS = {"ILS": "₪", "EUR": "€", "USD": "$", "GBP": "£"}

TEXT: dict[str, dict[str, str]] = {
    "he": {
        "site_title": "SPOTX · כל האירועים בישראל",
        "site_desc": "כל האירועים, המסיבות וההופעות בכל הארץ במקום אחד: תאריכים, ליינאפ, מחירי כרטיסים, כניסה חופשית, מקומות ומפה. מתעדכן כל הזמן.",
        "upcoming": "אירועים קרובים בכל הארץ",
        "venues": "מקומות אירועים בכל הארץ",
        "artists": "אמנים ו־DJs בישראל",
        "map": "מפת האירועים של ישראל",
        "abroad": "אירועים בחו״ל: הופעות ופסטיבלים בעולם",
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
        "site_title": "SPOTX · Every event in Israel",
        "site_desc": "Every event, party and show in Israel in one place: dates, lineups, ticket prices, free entry, venues and a map. Always up to date.",
        "upcoming": "Upcoming events in Israel",
        "venues": "Event venues in Israel",
        "artists": "Artists & DJs playing Israel",
        "map": "Israel events map",
        "abroad": "Events abroad: concerts and festivals worldwide",
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
    "karaoke": "קריוקי", "hi_tech": "הייטק", "conference": "כנסים", "classical_opera": "קלאסית / אופרה",
    "ai": "AI ובינה מלאכותית", "cyber": "סייבר", "software_dev": "פיתוח תוכנה", "data": "דאטה", "cloud_devops": "ענן ו-DevOps",
    "startups": "סטארטאפים ויזמות", "product": "ניהול מוצר", "gaming": "גיימינג", "web3": "בלוקצ׳יין ו-Web3",
    "cinema": "קולנוע וסרטים", "happy_hour": "האפי האוור", "dark_wave": "דארק ווייב / סינת׳", "yoga_wellness": "יוגה ורוחניות",
    "liberal": "ליברלי / חילופי זוגות", "kink": "בדסמ / פטיש", "sensual": "טנטרה וחושניות", "gender_balance": "איזון מגדרי",
    "festival": "פסטיבלים", "meetups": "מפגשים", "culture": "תרבות", "exhibitions": "תערוכות", "workshops": "סדנאות",
    "markets": "שווקים וקניות", "food": "אוכל", "environment": "סביבה", "religious": "דת ומסורת", "holidays": "חגים",
    "sport": "ספורט", "theatre": "תיאטרון", "kids": "ילדים ומשפחה", "lectures": "הרצאות והעשרה", "tours": "סיורים וטיולים",
    "dance": "מחול", "ballroom": "ריקודים סלוניים", "folk_dance": "ריקודי עם", "latin_dance": "ריקודים לטיניים", "salsa": "סלסה",
    "escape_rooms": "חדרי בריחה", "attractions": "אטרקציות", "coffee_carts": "עגלות קפה", "fine_dining": "מסעדות שף",
    "work_spaces": "חללי עבודה", "volunteering": "התנדבות", "team_activities": "פעילויות לצוות", "nightclubs": "מועדונים",
    "pools": "בריכות ופארקי מים", "pool_party": "מסיבות בריכה",
    "g_parties": "מסיבות ומועדונים", "g_live": "הופעות ומוזיקה חיה", "g_stage": "במה ומסך", "g_culture": "תרבות והעשרה",
    "g_kids": "ילדים ומשפחה", "g_food": "אוכל ושתייה", "g_leisure": "פנאי וספורט", "g_tech": "הייטק ועסקים",
    "g_community": "קהילה ו-LGBTQ+",
}  # fmt: skip
GENRES_EN_FIX = {"rnb": "R&B", "edm": "EDM", "hip_hop": "Hip Hop", "lgbt": "LGBTQ+", "reggaeton_latin": "Reggaeton / Latin",
                 "stand_up": "Stand-up comedy", "live_show": "Live music",
                 "hi_tech": "Hi-tech", "classical_opera": "Classical / Opera", "ai": "AI", "cinema": "Cinema & Movies",
                 "software_dev": "Software development", "cloud_devops": "Cloud & DevOps", "web3": "Web3 & Blockchain",
                 "happy_hour": "Happy hour", "dark_wave": "Dark Wave / Synth", "yoga_wellness": "Yoga & Wellness",
                 "liberal": "Liberal / Swingers", "kink": "BDSM / Fetish", "sensual": "Tantra & Sensual",
                 "gender_balance": "Gender balanced",
                 "kids": "Kids & Family", "lectures": "Lectures & Talks", "tours": "Tours & Trips",
                 "ballroom": "Ballroom Dancing", "folk_dance": "Folk Dancing", "latin_dance": "Latin Dancing",
                 "team_activities": "Team activities", "nightclubs": "Clubs",
                 "pools": "Pools & Water parks", "pool_party": "Pool parties",
                 "g_parties": "Parties & Clubs", "g_live": "Live Music", "g_stage": "Stage & Screen",
                 "g_culture": "Culture & Learning", "g_kids": "Kids & Family", "g_food": "Food & Drinks",
                 "g_leisure": "Leisure & Sport", "g_tech": "Tech & Business", "g_community": "Community & LGBTQ+"}  # fmt: skip
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
    langs: tuple[str, ...] = ("he", "en")  # languages the content exists in (untranslated blog posts: Hebrew only)


# --------------------------------------------------------------------------- helpers
def _url(cfg: Settings, path: str, lang: str = "he") -> str:
    return f"{cfg.site.base_url}{path}" + ("?lang=en" if lang == "en" else "")


def event_path(row: EventRow) -> str:
    return f"/event/{quote(row.slug or row.id, safe='')}"


def go_path(event_id: str) -> str:
    """Our redirect to the seller's page (GET /go/...): ticket links are handed out one click at a
    time, rate-limited, and never appear in bulk in the API or the pages."""
    return f"/go/{quote(event_id, safe='')}"


def _cover(cfg: Settings, row: EventRow) -> str | None:
    if row.cover_url:
        return cfg.site.base_url + row.cover_url
    return row.image_url


def _when(row: EventRow, cfg: Settings, lang: str) -> str:
    if row.start_at is None:
        return ""
    if not row.time_known and row.local_date:  # a date alone: no made-up midnight
        return f"{DAYS[lang][row.local_date.weekday()]} {row.local_date:%d/%m/%Y}"
    local = row.start_at.astimezone(ZoneInfo(row.time_zone or cfg.timezone))  # abroad: the venue's own time
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


def _iso(dt: datetime | None, cfg: Settings, row: EventRow) -> str | None:
    """A time in the venue's zone; a date alone (time unknown) as the date only."""
    if not row.time_known and row.local_date and dt is row.start_at:
        return row.local_date.isoformat()
    return dt.astimezone(ZoneInfo(row.time_zone or cfg.timezone)).isoformat() if dt else None


def _upcoming(session: Session, *where: Any, limit: int = LIST_LIMIT, abroad: bool = False) -> list[EventRow]:
    return list(
        session.scalars(
            select(EventRow)
            .where(upcoming_filter(), EventRow.country.is_not(None) if abroad else EventRow.country.is_(None), *where)
            .order_by(EventRow.start_at.asc().nulls_first())
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
            # An event abroad is in its own country (ZZ: unknown, left out), not Israel.
            "addressCountry": "IL" if not row.country else None if row.country == "ZZ" else row.country,
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
            "url": _url(cfg, go_path(row.id)),
            # On sale since: the seller's own sale start, else when we first saw it on sale.
            "validFrom": _iso(t.sale_start or row.first_seen_at, cfg, row),
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
            "url": _url(cfg, go_path(row.id)),
            "availability": "https://schema.org/SoldOut" if row.is_sold_out else "https://schema.org/InStock",
            "validFrom": _iso(row.first_seen_at, cfg, row),
        }
    # No price known: still where to buy and whether it sells (Google wants offers on every event); a
    # free entry is price 0, otherwise no made-up price.
    return {
        "@type": "Offer",
        "url": _url(cfg, go_path(row.id)),
        "availability": "https://schema.org/SoldOut" if row.is_sold_out else "https://schema.org/InStock",
        "validFrom": _iso(row.first_seen_at, cfg, row),
        **({"price": 0, "priceCurrency": row.currency or "ILS"} if row.entry_type == "free" else {}),
    }


def _organizer(row: EventRow, cfg: Settings) -> dict[str, Any] | None:
    """Who puts the event on: the organizer the seller names, with its ticket page as the link (we keep
    no organizer sites); without one, the venue (a club runs its own nights), with its page here."""
    if row.organizer:
        return {"@type": "Organization", "name": row.organizer, "url": _url(cfg, go_path(row.id))}
    if row.venue_name:
        return {"@type": "Organization", "name": row.venue_name, "url": _url(cfg, f"/venue/{quote(row.venue_name, safe='')}")}
    # Neither (16 of 6,680, all Geektime's own conferences): the site that runs and sells it, by its
    # source name (as the tickets link shows it); no seller address in the page, only our /go/ redirect.
    return {"@type": "Organization", "name": row.source, "url": _url(cfg, go_path(row.id))}


def _end(row: EventRow, cfg: Settings) -> str | None:
    """The end the seller gives; without one, the event's own day (a date: no made-up hour)."""
    return _iso(row.end_at, cfg, row) or (row.local_date.isoformat() if row.local_date else None)


def event_jsonld(row: EventRow, cfg: Settings) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "MusicEvent" if row.lineup or row.genres else "Event",
        "@id": _url(cfg, event_path(row)) + "#event",
        "name": row.title,
        "url": _url(cfg, event_path(row)),
        "startDate": _iso(row.start_at, cfg, row),
        "endDate": _end(row, cfg),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": _place(row),
        "image": [_cover(cfg, row) or f"{cfg.site.base_url}/og-default.png?v=spotx2"],  # the page's own og:image fallback
        # Google wants one on every event: the seller's text, else the page's own summary (when, where).
        "description": _clip(row.description) or _clip(_summary(row, cfg, "he")),
        "inLanguage": "he",
        "isAccessibleForFree": row.entry_type == "free" or None,
        "typicalAgeRange": f"{row.min_age}-" if row.min_age else None,
        "keywords": ", ".join(row.genres) or None,
        # The lineup; without one, whoever puts the event on (a theatre's own show, a club night).
        "performer": [{"@type": "Person", "name": n, "url": _url(cfg, f"/artist/{quote(n, safe='')}")} for n in row.lineup]
        or [o for o in [_organizer(row, cfg)] if o]
        or None,
        "organizer": _organizer(row, cfg),
        "offers": _offers(row, cfg),
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
        "logo": cfg.site.base_url + "/icon-512.png?v=spotx2",
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
def _where_to(session: Session, cfg: Settings, lang: str) -> list[tuple[str, str]]:
    """The home page's own question, "where to go out?", answered from tonight's and this weekend's listings."""
    he, qa = lang == "he", []
    for when, ask in (("tonight", ("לאן יוצאים הערב?", "Where to go out tonight?")),
                      ("weekend", ("לאן יוצאים בסוף השבוע?", "Where to go out this weekend?"))):  # fmt: skip
        rows = _upcoming(session, *_when_where(when, cfg), limit=500)
        if not rows:
            continue
        top = ", ".join(dict.fromkeys(r.title for r in rows[:5]))
        genres = ", ".join(genre_label(g, lang) for g, _ in Counter(g for r in rows for g in r.genres or []).most_common(3))
        qa.append((ask[0 if he else 1], (
            f"יש {len(rows)} אירועים ב-{cfg.site.name}, למשל: {top}." + (f" הכי הרבה: {genres}." if genres else "")
            + " בוחרים מתי, איפה (או לידי), קטגוריה ושעה, ומקבלים רק את מה שמתאים."
            if he else f"{cfg.site.name} lists {len(rows)} events, such as {top}." + (f" Most popular: {genres}." if genres else "")
            + " Pick when, where (or near me), genre and time, and get only what fits."
        )))  # fmt: skip
    return qa


def home_page(session: Session, cfg: Settings, lang: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session)
    faq = _where_to(session, cfg, lang)
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
    }
    faq_html = "".join(f"<h2>{escape(q)}</h2><p>{escape(a)}</p>" for q, a in faq)
    return Page(
        title=t["site_title"],
        description=t["site_desc"],
        path="/",
        lang=lang,
        jsonld=[*_website(cfg, lang), _item_list(rows, cfg, t["upcoming"]), *([faq_ld] if faq else [])],
        body=f"<h1>{escape(t['site_title'])}</h1><p>{escape(t['site_desc'])}</p>{faq_html}{_landing_links(session, cfg, lang)}"
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
            f'<p><a href="{escape(go_path(row.id))}?at=seo" rel="nofollow noopener">{t["tickets"]} ({escape(row.source)})</a></p></article>'
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
        place["event"] = [{"@id": _url(cfg, event_path(r)) + "#event", "name": r.title, "startDate": _iso(r.start_at, cfg, r)} for r in rows]
    return Page(
        title=f"{name} · {t['upcoming']} · {cfg.site.name}",
        description=t["venue_desc"].format(name=name),
        path=path,
        lang=lang,
        image=_cover(cfg, rows[0]) if rows else None,
        jsonld=[place, _breadcrumbs(cfg, lang, (t["venues"], "/venues"), (name, path))],
        body=f"<h1>{escape(name)}</h1><p>{escape(t['venue_desc'].format(name=name))}</p>" + _event_list_html(rows, cfg, lang)
        + _links_html(_related(session, cfg, lang, path), lang),
        noindex=not rows,
    )


def artist_page(session: Session, cfg: Settings, lang: str, name: str) -> Page:
    t = TEXT[lang]
    rows = _upcoming(session, lineup_contains(name))
    path = f"/artist/{quote(name, safe='')}"
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "url": _url(cfg, path),
        "performerIn": [{"@id": _url(cfg, event_path(r)) + "#event", "name": r.title, "startDate": _iso(r.start_at, cfg, r)} for r in rows],
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
            .order_by(EventRow.start_at.asc().nulls_first())
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
        body=f"<h1>{escape(t['abroad'])}</h1>" + _event_list_html(rows, cfg, lang) + _links_html(_related(session, cfg, lang, "/abroad"), lang),
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
        items = [a.coordinator, a.phone]  # never the email: the page reveals it on a click (harvesters)
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
        f"{cfg.site.name} פועל לפי חוק הגנת הפרטיות, התשמ\"א-1981 (כולל תיקון 13). אין באתר הרשמה "
        "ועוגיות פרסום; נשמרים בדפדפן רק פריטים הכרחיים, וסטטיסטיקת גלישה (Google Analytics) ותוכן מצד שלישי "
        "(מפה) נטענים רק בהסכמה."
        if he
        else f"{cfg.site.name} follows the Israeli Privacy Protection Law, 1981 (including Amendment 13). There is no "
        "sign-up and no advertising cookies; only necessary items are stored in the browser, and usage statistics "
        "(Google Analytics) and third-party content (the map) load only with consent."
    )
    contact = ""  # the address is revealed on the page by a click, never in the HTML (harvesters)
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


# About, FAQ, contact and partnerships: the React app (web/src/pages/Info.tsx) has the full text;
# crawlers that don't run JavaScript get this summary. path -> ((title he, en), (text he, en)).
INFO_PAGES = {
    "/about": (("אודות", "About"), (
        "{name} אוסף את כל האירועים, המסיבות וההופעות בישראל מעשרות אתרי כרטיסים למקום אחד, כדי שיהיה קל למצוא לאן יוצאים.",
        "{name} collects every event, party and show in Israel from dozens of ticketing sites into one place, so finding where to go out is easy.",
    )),
    "/faq": (("שאלות נפוצות", "FAQ"), (
        "תשובות לשאלות נפוצות על {name}: מאיפה מגיעים האירועים, האם אנחנו מוכרים כרטיסים, כמה זה עולה ואיך מוסיפים אירוע.",
        "Answers to common questions about {name}: where the events come from, whether we sell tickets, what it costs and how to add an event.",
    )),
    "/contact": (("צור קשר", "Contact us"), (
        "שאלה, הצעה, טעות באירוע או אירוע חסר? כתבו ל-{name} ונחזור אליכם.",
        "A question, a suggestion, a wrong detail or a missing event? Write to {name} and we'll get back to you.",
    )),
    "/stores": (("חנויות הכרטיסים", "Ticket stores"), (
        "כל אתרי הכרטיסים, המועדונים והיכלי התרבות ש-{name} אוסף מהם אירועים, וכמה אירועים קרובים יש לכל אחד.",
        "Every ticketing site, club and culture hall {name} collects events from, and how many upcoming events each one has.",
    )),
    "/partners": (("שיתופי פעולה", "Partnerships"), (
        "מפיקים, מקומות, אתרי כרטיסים ומותגים: בואו נעבוד יחד כדי שהאירועים שלכם יגיעו לעוד אנשים ב-{name}.",
        "Promoters, venues, ticketing sites and brands: let's work together to bring your events to more people on {name}.",
    )),
}


def info_page(path: str) -> Any:
    def build(session: Session, cfg: Settings, lang: str) -> Page:
        i = 0 if lang == "he" else 1
        title, text = INFO_PAGES[path][0][i], INFO_PAGES[path][1][i].format(name=cfg.site.name)
        return Page(
            title=f"{title} · {cfg.site.name}",
            description=text,
            path=path,
            lang=lang,
            jsonld=[_breadcrumbs(cfg, lang, (title, path))],
            body=f"<h1>{escape(title)}</h1><p>{escape(text)}</p>",  # no email: revealed on the page by a click
        )

    return build


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


# A town that is not in the config (GeoNames, places.py) gets a city page only with this many upcoming
# events: the busiest towns (Shoham, Ganei Tikva, Tiberias...), not every village with a show or two.
TOWN_PAGE_MIN = 10


def _page_cities(cfg: Settings) -> list[CitySettings]:
    """Configured cities, then every other Israeli town (GeoNames), with a plain-ASCII URL key ("yavne", not "yavné")."""
    configured = {c.key for c in cfg.enrichment.cities}
    return [c if c.key in configured else c.model_copy(update={"key": _ascii_slug(c.key)})
            for c in all_cities(cfg.enrichment.cities, cfg.enrichment.places_min_population)]  # fmt: skip


def _ascii_slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


_FACETS_LOCK = threading.Lock()


@cache
def _facets() -> TTLCache[tuple[Any, ...], Any]:
    return TTLCache(maxsize=256, ttl=get_settings().api.cache_ttl_seconds)


def _facet[T](key: tuple[Any, ...], build: Callable[[], T]) -> T:
    """Which cities, genres, regions and countries have pages: every SEO page asks, several times,
    and each answer scans all upcoming events (thousands of scans an hour). Reused for
    ``api.cache_ttl_seconds``, the age a cached page may reach anyway. Callers never mutate it."""
    with _FACETS_LOCK:
        if key in _facets():
            return _facets()[key]
    value = build()
    with _FACETS_LOCK:
        _facets()[key] = value
    return value


def city_pages(session: Session, cfg: Settings, when: str | None = None) -> list[CitySettings]:
    """Cities with enough upcoming events (tonight / this weekend) for a page."""

    def build() -> list[CitySettings]:
        counts = Counter(session.scalars(select(EventRow.city).where(upcoming_filter(), *_when_where(when, cfg))))
        configured = {c.key for c in cfg.enrichment.cities}
        return [c for c in _page_cities(cfg) if counts[c.he] >= (GENRE_PAGE_MIN if c.key in configured else TOWN_PAGE_MIN)]

    return _facet(("city_pages", id(cfg), when), build)


def city_genre_counts(session: Session, cfg: Settings) -> dict[str, dict[str, int]]:
    """City (Hebrew name) -> genres with enough upcoming events there for a /city/<key>/<genre> page."""

    def build() -> dict[str, dict[str, int]]:
        counts: dict[str, Counter[str]] = {}
        rows = session.execute(select(EventRow.city, EventRow.genres).where(upcoming_filter(), EventRow.country.is_(None), EventRow.city.is_not(None)))
        for city, genres in rows:
            if city:
                counts.setdefault(city, Counter()).update(cfg.enrichment.with_parents(genres or []))
        return {c: {g: n for g, n in cnt.most_common() if n >= GENRE_PAGE_MIN} for c, cnt in counts.items()}

    return _facet(("city_genre_counts", id(cfg)), build)


# Regions of the country with a /region/<key> page: (name, "in <name>" in Hebrew, English name).
# Tel Aviv and Eilat are cities with their own pages.
REGION_NAMES = {
    "gush_dan": ("גוש דן והמרכז", "בגוש דן ובמרכז", "Gush Dan and the Center"),
    "sharon": ("השרון", "בשרון", "the Sharon"),
    "jerusalem": ("ירושלים והסביבה", "בירושלים והסביבה", "the Jerusalem area"),
    "haifa": ("חיפה והקריות", "בחיפה ובקריות", "Haifa and the Krayot"),
    "galilee": ("הצפון והגליל", "בצפון ובגליל", "the North and Galilee"),
    "negev": ("הדרום והנגב", "בדרום ובנגב", "the South and the Negev"),
}


def region_pages(session: Session) -> list[str]:
    def build() -> list[str]:
        counts = Counter(session.scalars(select(EventRow.area).where(upcoming_filter(), EventRow.country.is_(None))))
        return [k for k in REGION_NAMES if counts[k] >= GENRE_PAGE_MIN]

    return _facet(("region_pages",), build)


def abroad_countries(session: Session) -> dict[str, str]:
    """URL slug (English name) -> country code, for countries with enough upcoming events for /abroad/<slug>."""

    def build() -> dict[str, str]:
        counts = Counter(session.scalars(select(EventRow.country).where(upcoming_filter(), EventRow.country.is_not(None))))
        out: dict[str, str] = {}
        for code, n in counts.most_common():
            if code and n >= GENRE_PAGE_MIN and is_country(code):
                out[_ascii_slug(country_name(code, "en"))] = code
        return out

    return _facet(("abroad_countries",), build)


def _nearby(city: CitySettings, cities: list[CitySettings], n: int = 4) -> list[CitySettings]:
    if not city.center:
        return []
    lat, lng = city.center
    others = [c for c in cities if c.center and c.key != city.key]
    return sorted(others, key=lambda c: (c.center[0] - lat) ** 2 + ((c.center[1] - lng) * 0.85) ** 2)[:n]  # type: ignore[index]


@cache
def _city_intros(path: Path = CONTENT_DIR / "city_intros.yaml") -> dict[str, dict[str, str]]:
    """Hand-written intro paragraph per city page (content/city_intros.yaml), if there is one."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}


def _landing_paths(session: Session, cfg: Settings) -> list[tuple[str, str, str]]:
    """(path, Hebrew name, English name) of every landing page that currently has enough events."""
    out = [("/tonight", "מה עושים הערב", "Tonight"), ("/weekend", "סוף השבוע", "This weekend")]
    cities = city_pages(session, cfg)
    out += [(f"/city/{c.key}", c.he, c.en) for c in cities]
    out += [(f"/genre/{g}", genre_label(g, "he"), genre_label(g, "en")) for g in genre_counts(session, cfg)]
    for when, he, en in (("tonight", "הערב", "tonight"), ("weekend", "בסופ״ש", "this weekend")):
        out += [(f"/genre/{g}/{when}", f"{genre_label(g, 'he')} {he}", f"{genre_label(g, 'en')} {en}")
                for g in genre_counts(session, cfg, when)]  # fmt: skip
        out += [(f"/city/{c.key}/{when}", f"{he} ב{c.he}", f"{c.en} {en}") for c in city_pages(session, cfg, when)]
    by_city = city_genre_counts(session, cfg)
    out += [(f"/city/{c.key}/{g}", f"{genre_label(g, 'he')} ב{c.he}", f"{genre_label(g, 'en')} in {c.en}")
            for c in cities for g in by_city.get(c.he, {})]  # fmt: skip
    out += [(f"/region/{k.replace('_', '-')}", f"אירועים {REGION_NAMES[k][1]}", f"Events in {REGION_NAMES[k][2]}") for k in region_pages(session)]
    out += [(f"/abroad/{slug}", country_name(code, "he"), country_name(code, "en")) for slug, code in abroad_countries(session).items()]
    return out


def _landing_text(kind: str, lang: str, label: str, place: str = "") -> tuple[str, str, str, str]:
    """(h1 / title, meta description, intro paragraph, FAQ scope). ``label`` is the genre or city name,
    ``place`` the "in <city / region / country>" phrase."""
    g, p = label, place
    if lang == "he":
        return {
            "tonight": ("מה עושים הערב? כל האירועים הערב בישראל",
                        "מה עושים הערב: כל המסיבות, ההופעות, הסטנדאפ וההצגות של הערב בישראל, עם שעות, מחירים, כניסה חופשית וכרטיסים.",
                        "כל האירועים שמתקיימים הערב, מעודכן כמה פעמים ביום מאתרי הכרטיסים בישראל.", "הערב"),
            "weekend": ("מה עושים בסוף השבוע? אירועים ומסיבות בסופ״ש",
                        "מסיבות, הופעות ואירועים לסוף השבוע הקרוב בישראל: חמישי, שישי ושבת, עם מחירים וכרטיסים.",
                        "כל האירועים של סוף השבוע הקרוב, מחמישי ועד מוצאי שבת.", "בסוף השבוע"),
            "genre": (f"{g}: אירועים ומסיבות קרובים בישראל",
                      f"כל אירועי ה{g} הקרובים בישראל: תאריכים, ליינאפ, מקומות, מחירי כרטיסים וכניסה חופשית.",
                      f"כל אירועי ה{g} הקרובים, מכל אתרי הכרטיסים במקום אחד.", f"בקטגוריית {g}"),
            "genre_tonight": (f"{g} הערב: כל אירועי ה{g} של הערב",
                              f"{g} הערב בישראל: כל אירועי ה{g} שמתקיימים הערב, עם שעות, מקומות, מחירים וכרטיסים.",
                              f"כל אירועי ה{g} של הערב, מעודכן כמה פעמים ביום.", f"בקטגוריית {g} הערב"),
            "genre_weekend": (f"{g} בסופ״ש: אירועי {g} בסוף השבוע הקרוב",
                              f"{g} בסוף השבוע: כל אירועי ה{g} של חמישי, שישי ושבת הקרובים בישראל, עם מחירים וכרטיסים.",
                              f"כל אירועי ה{g} של סוף השבוע הקרוב, מחמישי ועד מוצאי שבת.", f"בקטגוריית {g} בסוף השבוע"),
            "city": (f"מה עושים {p}? אירועים ומסיבות {p}",
                     f"כל האירועים, המסיבות וההופעות הקרובים {p}: תאריכים, ליינאפ, מקומות, מחירי כרטיסים וכניסה חופשית.",
                     f"כל האירועים הקרובים {p}, מכל אתרי הכרטיסים במקום אחד.", p),
            "city_tonight": (f"מה עושים הערב {p}? כל האירועים הערב {p}",
                             f"כל המסיבות, ההופעות והאירועים של הערב {p}: שעות, מקומות, מחירים, כניסה חופשית וכרטיסים.",
                             f"כל האירועים שמתקיימים הערב {p}, מעודכן כמה פעמים ביום.", f"הערב {p}"),
            "city_weekend": (f"מה עושים בסופ״ש {p}? אירועים בסוף השבוע {p}",
                             f"מסיבות, הופעות ואירועים לסוף השבוע הקרוב {p}: חמישי, שישי ושבת, עם מחירים וכרטיסים.",
                             f"כל האירועים של סוף השבוע הקרוב {p}, מחמישי ועד מוצאי שבת.", f"בסוף השבוע {p}"),
            "city_genre": (f"{g} {p}: כל האירועים הקרובים",
                           f"כל אירועי ה{g} הקרובים {p}: תאריכים, ליינאפ, מקומות, מחירי כרטיסים וכניסה חופשית.",
                           f"כל אירועי ה{g} {p}, מכל אתרי הכרטיסים במקום אחד.", f"בקטגוריית {g} {p}"),
            "region": (f"אירועים {p}: מה עושים {p}",
                       f"כל האירועים, המסיבות וההופעות הקרובים {p}: תאריכים, מקומות, מחירי כרטיסים וכניסה חופשית.",
                       f"כל האירועים הקרובים {p}, מכל אתרי הכרטיסים במקום אחד.", p),
            "abroad_country": (f"אירועים {p}: הופעות ופסטיבלים {p}",
                               f"כרטיסים וחבילות להופעות, מסיבות ופסטיבלים {p}: תאריכים, מקומות ומחירים.",
                               f"כל ההופעות והפסטיבלים הקרובים {p}.", p),
        }[kind]  # fmt: skip
    return {
        "tonight": ("What's on tonight in Israel",
                    "Everything happening tonight in Israel: parties, live music, stand-up and shows, with times, prices, free entry and tickets.",
                    "Every event happening tonight, refreshed several times a day from Israeli ticketing sites.", "tonight"),
        "weekend": ("What's on this weekend in Israel",
                    "Parties, concerts and events for the coming weekend in Israel (Thursday to Saturday), with prices and tickets.",
                    "Every event this coming weekend, Thursday through Saturday night.", "this weekend"),
        "genre": (f"{g} events in Israel",
                  f"Upcoming {g} events in Israel: dates, lineups, venues, ticket prices and free entry.",
                  f"Every upcoming {g} event, from every ticketing site in one place.", f"for {g}"),
        "genre_tonight": (f"{g} tonight in Israel",
                          f"{g} events happening tonight in Israel, with times, venues, prices and tickets.",
                          f"Every {g} event tonight, refreshed several times a day.", f"for {g} tonight"),
        "genre_weekend": (f"{g} this weekend in Israel",
                          f"{g} events this coming weekend in Israel (Thursday to Saturday), with prices and tickets.",
                          f"Every {g} event this coming weekend, Thursday through Saturday night.", f"for {g} this weekend"),
        "city": (f"What's on {p}: events and parties",
                 f"Upcoming events, parties and shows {p}: dates, lineups, venues, ticket prices and free entry.",
                 f"Every upcoming event {p}, from every ticketing site in one place.", p),
        "city_tonight": (f"What's on tonight {p}",
                         f"Everything happening tonight {p}: parties, concerts and shows, with times, venues, prices and tickets.",
                         f"Every event tonight {p}, refreshed several times a day.", f"{p} tonight"),
        "city_weekend": (f"What's on this weekend {p}",
                         f"Parties, concerts and events this coming weekend {p} (Thursday to Saturday), with prices and tickets.",
                         f"Every event this coming weekend {p}, Thursday through Saturday night.", f"{p} this weekend"),
        "city_genre": (f"{g} events {p}",
                       f"Upcoming {g} events {p}: dates, lineups, venues, ticket prices and free entry.",
                       f"Every upcoming {g} event {p}, from every ticketing site in one place.", f"for {g} {p}"),
        "region": (f"Events {p}: what's on",
                   f"Upcoming events, parties and shows {p}: dates, venues, ticket prices and free entry.",
                   f"Every upcoming event {p}, from every ticketing site in one place.", p),
        "abroad_country": (f"Events {p}: concerts and festivals",
                           f"Tickets and packages for concerts, parties and festivals {p}: dates, venues and prices.",
                           f"Every upcoming concert and festival {p}.", p),
    }[kind]  # fmt: skip


def _faq(rows: list[EventRow], lang: str, scope: str, site: str, tonight: list[EventRow] | None = None) -> list[tuple[str, str]]:
    """Questions people ask, answered from the live listings (what AI answer engines quote).
    ``tonight``: the place's events tonight, for "where to go out tonight in <place>?"."""
    he = lang == "he"
    free = [r for r in rows if r.entry_type == "free" or r.min_price == 0]
    priced = sorted((r for r in rows if r.min_price), key=lambda r: r.min_price or 0)
    venues = [v for v, _ in Counter(r.venue_name for r in rows if r.venue_name).most_common(3)]
    qa = [(
        f"כמה אירועים יש {scope}?" if he else f"How many events are on {scope}?",
        f"כרגע יש {len(rows)} אירועים {scope} ב-{site}." if he else f"There are currently {len(rows)} events {scope} on {site}.",
    )]  # fmt: skip
    if tonight:
        names = ", ".join(dict.fromkeys(r.title for r in tonight[:5]))
        qa.append((
            f"לאן יוצאים הערב {scope}?" if he else f"Where to go out tonight {scope}?",
            f"הערב יש {len(tonight)} אירועים {scope}, למשל: {names}." if he else f"There are {len(tonight)} events {scope} tonight, such as {names}.",
        ))  # fmt: skip
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


@dataclass
class Landing:
    kind: str
    path: str
    label: str = ""  # genre, city or country name
    place: str = ""  # "in <city / region / country>"
    where: list[Any] = field(default_factory=list)
    city: CitySettings | None = None
    region: str | None = None
    genre: str | None = None
    abroad: bool = False


def resolve_landing(session: Session, cfg: Settings, lang: str, path: str) -> Landing | None:
    """The landing page at ``path`` (/tonight, /genre/techno/weekend, /city/herzliya/techno, /region/sharon,
    /abroad/greece...), or None when there is no such page or it has too few events."""
    he = lang == "he"
    head, *rest = path.strip("/").split("/")
    when: str | None = None
    if head in WHENS and not rest:
        land = Landing(head, path)
        when = head
    elif head == "genre" and len(rest) in (1, 2):
        when = rest[1] if len(rest) == 2 else None
        if (when and when not in WHENS) or rest[0] not in genre_counts(session, cfg, when):
            return None
        land = Landing(f"genre_{when}" if when else "genre", path, genre_label(rest[0], lang), genre=rest[0])
        land.where.append(_genre_filter(rest[0], cfg))
    elif head == "city" and len(rest) in (1, 2):
        city = next((c for c in _page_cities(cfg) if c.key == rest[0]), None)
        if city is None:
            return None
        sub = rest[1] if len(rest) == 2 else None
        when = sub if sub in WHENS else None
        place = f"ב{city.he}" if he else f"in {city.en}"
        if sub is None or when:
            if city.key not in {c.key for c in city_pages(session, cfg, when)}:
                return None
            land = Landing(f"city_{when}" if when else "city", path, city.he if he else city.en, place, city=city)
        else:
            if city.key not in {c.key for c in city_pages(session, cfg)} or sub not in city_genre_counts(session, cfg).get(city.he, {}):
                return None
            land = Landing("city_genre", path, genre_label(sub, lang), place, city=city, genre=sub)
            land.where.append(_genre_filter(sub, cfg))
        land.where.append(EventRow.city == city.he)
    elif head == "region" and len(rest) == 1:
        region = rest[0].replace("-", "_")
        if region not in region_pages(session):
            return None
        name = REGION_NAMES[region]
        land = Landing("region", path, name[0] if he else name[2], name[1] if he else f"in {name[2]}", [EventRow.area == region], region=region)
    elif head == "abroad" and len(rest) == 1:
        code = abroad_countries(session).get(rest[0])
        if code is None:
            return None
        name = country_name(code, lang)
        land = Landing("abroad_country", path, name, f"ב{name}" if he else f"in {name}", [EventRow.country == code], abroad=True)
    else:
        return None
    land.where = _when_where(when, cfg) + land.where
    return land


def _related(session: Session, cfg: Settings, lang: str, path: str) -> list[tuple[str, str]]:
    """Links from a page to its neighbours: a city's dates, genres, venues and nearby cities; a region's
    cities; a genre's cities; a venue's city; other countries abroad. Every target has a page."""
    he = lang == "he"
    out: list[tuple[str, str]] = []
    cities = city_pages(session, cfg)
    head, *rest = path.strip("/").split("/")

    def city_links(c: CitySettings) -> None:
        out.append((f"/city/{c.key}", f"כל האירועים ב{c.he}" if he else f"All events in {c.en}"))
        for when, w_he, w_en in (("tonight", "הערב", "tonight"), ("weekend", "בסופ״ש", "this weekend")):
            if c.key in {x.key for x in city_pages(session, cfg, when)}:
                out.append((f"/city/{c.key}/{when}", f"{w_he} ב{c.he}" if he else f"{c.en} {w_en}"))

    if head == "city" and rest:
        c = next((x for x in cities if x.key == rest[0]), None)
        if c:
            city_links(c)
            in_c = f"ב{c.he}" if he else f"in {c.en}"
            out += [(f"/city/{c.key}/{g}", f"{genre_label(g, lang)} {in_c}") for g in list(city_genre_counts(session, cfg).get(c.he, {}))[:12]]
            venues = Counter(session.scalars(select(EventRow.venue_name).where(upcoming_filter(), EventRow.city == c.he, EventRow.venue_name.is_not(None))))
            out += [(f"/venue/{quote(v, safe='')}", v) for v, _ in venues.most_common(8) if v]
            out += [(f"/city/{n.key}", n.he if he else n.en) for n in _nearby(c, cities)]
    elif head == "region" and rest:
        region = rest[0].replace("-", "_")
        counts = Counter(session.scalars(select(EventRow.city).where(upcoming_filter(), EventRow.area == region)))
        out += [(f"/city/{c.key}", c.he if he else c.en) for c in sorted(cities, key=lambda c: -counts[c.he]) if counts[c.he]]
    elif head == "genre" and rest:
        by_city = city_genre_counts(session, cfg)
        out += [(f"/city/{c.key}/{rest[0]}", f"{genre_label(rest[0], lang)} {'ב' + c.he if he else 'in ' + c.en}") for c in cities if rest[0] in by_city.get(c.he, {})]
    elif head == "venue" and rest:
        row = session.scalars(select(EventRow.city).where(upcoming_filter(), EventRow.venue_name.ilike(f"%{unquote(rest[0])}%"), EventRow.city.is_not(None)).limit(1)).first()
        c = next((x for x in cities if x.he == row), None)
        if c:
            city_links(c)
    elif head == "abroad":
        out += [(f"/abroad/{slug}", country_name(code, lang)) for slug, code in abroad_countries(session).items()]
    if head in ("city", "genre", "region", "tonight", "weekend"):
        out += [(f"/region/{k.replace('_', '-')}", REGION_NAMES[k][0] if he else REGION_NAMES[k][2].removeprefix("the ").capitalize()) for k in region_pages(session)]
    return [x for x in dict.fromkeys(out) if x[0] != path]


def _links_html(links: list[tuple[str, str]], lang: str) -> str:
    if not links:
        return ""
    items = "".join(f'<li><a href="{escape(p)}">{escape(n)}</a></li>' for p, n in links)
    return f"<nav><h2>{'עוד באזור' if lang == 'he' else 'Explore more'}</h2><ul>{items}</ul></nav>"


def landing_info(session: Session, cfg: Settings, lang: str, path: str) -> dict[str, Any]:
    """Title, intro and related links of a page, for the web app (the same the server renders)."""
    land = resolve_landing(session, cfg, lang, path)
    links = [{"path": p, "label": n} for p, n in _related(session, cfg, lang, path)]
    if land is None:
        return {"title": None, "intro": None, "links": links, "country": None, "city": None}
    title, _, intro, _ = _landing_text(land.kind, lang, land.label, land.place)
    country = abroad_countries(session).get(path.rsplit("/", 1)[-1]) if land.abroad else None
    city = {"he": land.city.he, "en": land.city.en} if land.city else None
    return {"title": title, "intro": _intro(land, lang) or intro, "links": links, "country": country, "city": city}


def _intro(land: Landing, lang: str) -> str | None:
    return _city_intros().get(land.city.key, {}).get(lang) if land.kind == "city" and land.city else None


def landing_page(session: Session, cfg: Settings, lang: str, path: str) -> Page | None:
    land = resolve_landing(session, cfg, lang, path)
    if land is None:
        return None
    rows = _upcoming(session, *land.where, limit=500, abroad=land.abroad)
    title, desc, intro, scope = _landing_text(land.kind, lang, land.label, land.place)
    intro = _intro(land, lang) or intro
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    tonight = [r for r in rows if r.local_date == today] if land.kind in ("city", "region") else None
    faq = _faq(rows, lang, scope, cfg.site.name, tonight)
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq],
    }
    jsonld = [_item_list(rows[:LIST_LIMIT], cfg, title), faq_ld, _breadcrumbs(cfg, lang, (title, path))]
    if land.city:
        c = land.city
        city_ld: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "City",
            "name": c.he if lang == "he" else c.en,
            "url": _url(cfg, f"/city/{c.key}", lang),
            "containedInPlace": {"@type": "Country", "name": "ישראל" if lang == "he" else "Israel"},
        }
        if c.center:
            city_ld["geo"] = {"@type": "GeoCoordinates", "latitude": c.center[0], "longitude": c.center[1]}
        jsonld.append(city_ld)
    faq_html = "".join(f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in faq)
    return Page(
        title=f"{title} · {cfg.site.name}",
        description=desc,
        path=path,
        lang=lang,
        image=_cover(cfg, rows[0]) if rows else None,
        jsonld=jsonld,
        body=f"<h1>{escape(title)}</h1><p>{escape(intro)}</p>" + _event_list_html(rows[:LIST_LIMIT], cfg, lang)
        + f"<section><h2>{'שאלות נפוצות' if lang == 'he' else 'FAQ'}</h2>{faq_html}</section>"
        + _links_html(_related(session, cfg, lang, path), lang),
    )


def _landing_links(session: Session, cfg: Settings, lang: str) -> str:
    """Links from the home page, so crawlers reach every landing page."""
    # City sub-pages (/city/x/techno) are linked from their city page, not from here.
    paths = [x for x in _landing_paths(session, cfg) if not (x[0].startswith("/city/") and x[0].count("/") == 3)]
    links = [(p, he if lang == "he" else en) for p, he, en in [*paths, ("/blog", "המגזין", "Magazine")]]
    return "<nav><ul>" + "".join(f'<li><a href="{p}">{escape(n)}</a></li>' for p, n in links) + "</ul></nav>"


# --------------------------------------------------------------------------- render
def _jsonld_tag(data: dict[str, Any]) -> str:
    # No "<" at all inside the element: "</script" would close it early, "<!--<script" would keep it open.
    text = json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{text}</script>'


def render(template: str, page: Page, cfg: Settings) -> str:
    canonical = _url(cfg, page.path, page.lang if page.lang in page.langs else page.langs[0])
    image = page.image or f"{cfg.site.base_url}/og-default.png?v=spotx2"
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
        *(f'<meta name="{name}" content="{escape(token)}" />' for name, token in (
            ("google-site-verification", cfg.site.verification.google), ("msvalidate.01", cfg.site.verification.bing),
            ("yandex-verification", cfg.site.verification.yandex)) if token),  # fmt: skip
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
# Google reads at most 50,000 URLs / 50 MB per sitemap file; the site has more, so /sitemap.xml is an index of parts.
SITEMAP_PART = 10_000


def sitemap_urls(session: Session, cfg: Settings) -> list[str]:
    """Every public page as a <url> entry (Hebrew and English apart, the blog once)."""
    rows = session.scalars(select(EventRow).where(upcoming_filter()).order_by(EventRow.start_at.asc().nulls_first())).all()
    paths: list[tuple[str, date | None]] = [("/", None), ("/map", None), ("/abroad", None), ("/venues", None), ("/artists", None), ("/accessibility", None), ("/privacy", None), ("/terms", None)]
    paths += [(p, None) for p in INFO_PAGES]
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
    # Blog: posts with an English version get both URLs, untranslated ones the Hebrew URL alone.
    from partylinks import blog

    posts = blog.load_posts(cfg)
    english = {p.slug for p in blog.load_posts(cfg, lang="en") if p.lang == "en"}
    blog_paths = [("/blog", posts[0].modified if posts else None, True)]
    blog_paths += [(f"/blog/category/{k}", None, True) for k in sorted({p.category for p in posts})]
    blog_paths += [(f"/blog/{p.slug}", p.modified, p.slug in english) for p in posts]
    for path, modified, both in blog_paths:
        he, en = escape(_url(cfg, path, "he")), escape(_url(cfg, path, "en"))
        lastmod = f"<lastmod>{modified:%Y-%m-%d}</lastmod>" if modified else ""
        if not both:
            urls.append(f"<url><loc>{he}</loc>{lastmod}</url>")
            continue
        alternates = (
            f'<xhtml:link rel="alternate" hreflang="he" href="{he}"/>'
            f'<xhtml:link rel="alternate" hreflang="en" href="{en}"/>'
            f'<xhtml:link rel="alternate" hreflang="x-default" href="{he}"/>'
        )
        urls += [f"<url><loc>{u}</loc>{lastmod}{alternates}</url>" for u in (he, en)]
    return urls


def sitemap_index(urls: list[str], cfg: Settings) -> str:
    parts = range(1, (len(urls) + SITEMAP_PART - 1) // SITEMAP_PART + 1)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"<sitemap><loc>{cfg.site.base_url}/sitemap-{n}.xml</loc></sitemap>" for n in parts)
        + "\n</sitemapindex>\n"
    )


def sitemap_part(urls: list[str], part: int) -> str | None:
    """Part 1, 2... of the URL list; None past the end."""
    chunk = urls[(part - 1) * SITEMAP_PART : part * SITEMAP_PART] if part >= 1 else []
    if not chunk:
        return None
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(chunk)
        + "\n</urlset>\n"
    )


def robots(cfg: Settings) -> str:
    private = "".join(f"Disallow: {p}\n" for p in ROBOTS_PRIVATE)
    ai = "".join(f"User-agent: {bot}\n" for bot in AI_CRAWLERS)
    blocked = "".join(f"User-agent: {bot}\n" for bot in cfg.security.blocked_bots)
    return (
        f"# Search engines and AI search assistants are welcome; AI-training and data crawlers are not.\n"
        f"User-agent: *\nAllow: /\n{private}\n{ai}Allow: /\n{private}\n"
        + (f"# Data harvesters and AI-training crawlers: not welcome (also refused by the server).\n{blocked}Disallow: /\n\n" if blocked else "")
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
        ("> Every event, party, show and club night in Israel, collected from Israeli "
        "ticketing sites and updated several times a day. For each event: date and time (Israel time, "
        "Asia/Jerusalem), venue and address, lineup / DJs, genres, minimum age, every ticket tier with "
        "its price in ILS (₪), free-entry flags, and a link to buy tickets on the original site."),
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
        "## Magazine (guides with live event lists; Hebrew, English with `?lang=en`)",
        *(f"- [{p.title}]({base}/blog/{p.slug}{'?lang=en' if p.lang == 'en' else ''}): {p.description}"
          for p in blog.load_posts(cfg, lang="en")),
        "",
        "## Data (JSON API, no key needed)",
        (f"- [Events]({base}/events): filters `date=YYYY-MM-DD`, `weekend=true`, `genre`, `artist`, `venue`, "
        "`city`, `free_entry=true`, `time_slot` (morning/afternoon/evening/night), `area` (south, rothschild, center, business, "
        "beach, north, jaffa, outside), `min_price`, `max_price`, `q`; paged with `page`, `page_size`"),
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

    # The URL list takes ~30 s at prod size, and a crawler opens the index and every part at once:
    # built from scratch per request, all of them timed out (Search Console: "Couldn't fetch").
    # Built once, reused for an hour, then rebuilt in the background while the old list is served.
    from partylinks.api import ResponseCache  # local: api imports this module

    sitemap_cache = ResponseCache(ttl=3600, max_stale=86400, follow_writes=False)
    sitemap_lock = threading.Lock()  # concurrent first requests wait for one build instead of starting their own

    def cached_urls(request: Request) -> list[str]:
        def build() -> bytes:
            with request.app.state.db.session(write=False) as session:
                return "\n".join(sitemap_urls(session, cfg(request))).encode()

        with sitemap_lock:
            return sitemap_cache.get(("sitemap", ""), build)[0].decode().split("\n")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml(request: Request) -> Response:
        return Response(sitemap_index(cached_urls(request), cfg(request)), media_type="application/xml")

    @app.get("/sitemap-{part}.xml", include_in_schema=False)
    def sitemap_part_xml(part: int, request: Request) -> Response:
        xml = sitemap_part(cached_urls(request), part)
        return Response(xml, media_type="application/xml") if xml else Response(status_code=404)

    if key := app.state.settings.site.indexnow.key:  # IndexNow ownership proof (partylinks.indexnow)
        app.get(f"/{key}.txt", include_in_schema=False)(lambda: PlainTextResponse(key))

    @app.get("/llms.txt", include_in_schema=False)
    def llms(request: Request) -> PlainTextResponse:
        with request.app.state.db.session(write=False) as session:
            return PlainTextResponse(llms_txt(session, cfg(request)), media_type="text/markdown; charset=utf-8")

    from partylinks import blog

    index = site_dir / "index.html"

    # Rendered pages are the same for every visitor; building one runs several whole-table queries
    # (~1.5 s for the home page at prod size, ~12 s for /artists). Served from the same kind of cache
    # as the API (stale entries are rebuilt in the background), so a visitor waits only on a first visit.
    from partylinks.api import ResponseCache  # local: api imports this module

    rendered = ResponseCache(ttl=HTML_TTL_S, max_bytes=HTML_CACHE_BYTES, follow_writes=False)  # scrapes write all day
    app.state.html_cache = rendered  # api.CachedPages serves hits before routing

    def page(request: Request, build: Any) -> HTMLResponse:
        lang = "en" if request.query_params.get("lang") == "en" else "he"
        settings, db = cfg(request), request.app.state.db

        def make() -> tuple[bytes, int]:
            with db.session(write=False) as session:
                result = build(session, settings, lang)
            if result is None:
                t = TEXT[lang]
                result = Page(t["not_found"], t["site_desc"], request.url.path, lang, noindex=True, status=404,
                              body=f"<h1>{t['not_found']}</h1>")  # fmt: skip
            return render(index.read_text(encoding="utf-8"), result, settings).encode(), result.status

        body, code = rendered.get((request.url.path, request.url.query), make)
        return HTMLResponse(body, status_code=code)

    @app.get("/landing-info", tags=["meta"])
    def landing_info_(path: str, request: Request, lang: str = "he") -> dict[str, Any]:
        """Title, intro and related links of a landing page (the web app shows what the server renders)."""
        with request.app.state.db.session(write=False) as session:
            return landing_info(session, cfg(request), "en" if lang == "en" else "he", path)

    blog.register(app, page if index.is_file() else None)
    if not index.is_file():
        return

    from partylinks.plans.routes import register_page

    register_page(app, page)  # /plan/<id>: the WhatsApp preview of a "who's coming?" link

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

    def info_route(build: Any) -> Any:
        def route(request: Request) -> HTMLResponse:
            return page(request, build)

        return route

    for path in INFO_PAGES:
        app.get(path, include_in_schema=False)(info_route(info_page(path)))

    @app.get("/map", include_in_schema=False)
    def map_(request: Request) -> HTMLResponse:
        return page(request, map_page)

    @app.get("/abroad", include_in_schema=False)
    def abroad(request: Request) -> HTMLResponse:
        return page(request, abroad_page)

    def landing(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: landing_page(s, c, lang, request.url.path.rstrip("/")))

    for path in ("/tonight", "/weekend", "/genre/{genre}", "/genre/{genre}/{when}", "/city/{city}", "/city/{city}/{sub}",
                 "/region/{region}", "/abroad/{country}"):  # fmt: skip
        app.get(path, include_in_schema=False)(landing)

    @app.get("/admin", include_in_schema=False)
    def admin(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: Page("Admin", "", "/admin", lang, noindex=True))
