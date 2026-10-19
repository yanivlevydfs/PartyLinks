"""Search, social and AI visibility for the React site.

The site renders in the browser, but crawlers, link previews and LLM fetchers often don't run
JavaScript. So every public page is served from here with its own ``<head>`` (title, description,
canonical, hreflang, Open Graph / Twitter, local geo tags), schema.org JSON-LD, and a plain-HTML
copy of the content inside ``#root`` that React replaces on load. Also serves ``sitemap.xml``,
``robots.txt`` and ``llms.txt``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session, selectinload

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter

TEL_AVIV = (32.0809, 34.7806)
LIST_LIMIT = 60  # events listed on index pages
LLMS_LIMIT = 400
DESC_LIMIT = 300
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
    "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "CCBot", "Bingbot",
]  # fmt: skip

TEXT = {
    "he": {
        "site_title": "PartyLinks · כל המסיבות בתל אביב",
        "site_desc": "כל המסיבות והאירועים בתל אביב במקום אחד: תאריכים, ליינאפ, מחירי כרטיסים, כניסה חופשית, מקומות ומפה. מתעדכן כל הזמן.",
        "upcoming": "מסיבות קרובות בתל אביב",
        "venues": "מקומות מסיבות בתל אביב",
        "artists": "אמנים ו־DJs בתל אביב",
        "map": "מפת מסיבות בתל אביב",
        "venue_desc": "מסיבות ואירועים קרובים ב{name}, תל אביב: תאריכים, ליינאפ וכרטיסים.",
        "artist_desc": "ההופעות והמסיבות הקרובות של {name} בתל אביב: תאריכים, מקומות וכרטיסים.",
        "free": "כניסה חופשית",
        "from": "החל מ־₪{p}",
        "lineup": "ליינאפ",
        "tickets": "לכרטיסים",
        "not_found": "העמוד לא נמצא",
        "days": ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"],
        "locale": "he_IL",
    },
    "en": {
        "site_title": "PartyLinks · Every Tel Aviv party",
        "site_desc": "Every party and club night in Tel Aviv in one place: dates, lineups, ticket prices, free entry, venues and a map. Always up to date.",
        "upcoming": "Upcoming parties in Tel Aviv",
        "venues": "Party venues in Tel Aviv",
        "artists": "Artists & DJs playing Tel Aviv",
        "map": "Tel Aviv party map",
        "venue_desc": "Upcoming parties and events at {name}, Tel Aviv: dates, lineups and tickets.",
        "artist_desc": "{name}'s upcoming shows and parties in Tel Aviv: dates, venues and tickets.",
        "free": "Free entry",
        "from": "From ₪{p}",
        "lineup": "Lineup",
        "tickets": "Tickets",
        "not_found": "Page not found",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "locale": "en_US",
    },
}


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
    return f"{TEXT[lang]['days'][local.weekday()]} {local:%d/%m/%Y %H:%M}"


def _price(row: EventRow, lang: str) -> str:
    if row.entry_type == "free" or row.min_price == 0:
        return TEXT[lang]["free"]
    return TEXT[lang]["from"].format(p=f"{float(row.min_price):g}") if row.min_price is not None else ""


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
            .where(upcoming_filter(), *where)
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
        "name": row.venue_name or row.address or row.city or "Tel Aviv",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": row.address or None,
            "addressLocality": row.city or "Tel Aviv",
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
    items = [("PartyLinks", "/"), *crumbs]
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
        "areaServed": {"@type": "City", "name": "Tel Aviv-Yafo", "address": {"@type": "PostalAddress", "addressCountry": "IL"}},
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
        body=f"<h1>{escape(t['site_title'])}</h1><p>{escape(t['site_desc'])}</p><h2>{escape(t['upcoming'])}</h2>"
        + _event_list_html(rows, cfg, lang),
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


# --------------------------------------------------------------------------- render
def _jsonld_tag(data: dict[str, Any]) -> str:
    # "</" inside a string would close the script element early.
    text = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return f'<script type="application/ld+json">{text}</script>'


def render(template: str, page: Page, cfg: Settings) -> str:
    canonical = _url(cfg, page.path, page.lang)
    image = page.image or f"{cfg.site.base_url}/og-default.png"
    t = TEXT[page.lang]
    other = "en" if page.lang == "he" else "he"
    lat, lon = TEL_AVIV
    head = [
        f"<title>{escape(page.title)}</title>",
        f'<meta name="description" content="{escape(page.description)}" />',
        f'<link rel="canonical" href="{escape(canonical)}" />',
        f'<link rel="alternate" hreflang="he" href="{escape(_url(cfg, page.path, "he"))}" />',
        f'<link rel="alternate" hreflang="en" href="{escape(_url(cfg, page.path, "en"))}" />',
        f'<link rel="alternate" hreflang="x-default" href="{escape(_url(cfg, page.path, "he"))}" />',
        '<meta name="robots" content="noindex, nofollow" />' if page.noindex
        else '<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1" />',
        f'<meta property="og:type" content="{page.og_type}" />',
        f'<meta property="og:site_name" content="{escape(cfg.site.name)}" />',
        f'<meta property="og:title" content="{escape(page.title)}" />',
        f'<meta property="og:description" content="{escape(page.description)}" />',
        f'<meta property="og:url" content="{escape(canonical)}" />',
        f'<meta property="og:image" content="{escape(image)}" />',
        f'<meta property="og:locale" content="{t["locale"]}" />',
        f'<meta property="og:locale:alternate" content="{TEXT[other]["locale"]}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{escape(page.title)}" />',
        f'<meta name="twitter:description" content="{escape(page.description)}" />',
        f'<meta name="twitter:image" content="{escape(image)}" />',
        # Local signals: the whole site is about Tel Aviv-Yafo.
        '<meta name="geo.region" content="IL-TA" />',
        '<meta name="geo.placename" content="Tel Aviv-Yafo" />',
        f'<meta name="geo.position" content="{lat};{lon}" />',
        f'<meta name="ICBM" content="{lat}, {lon}" />',
        '<link rel="alternate" type="text/plain" title="llms.txt" href="/llms.txt" />',
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
    paths: list[tuple[str, datetime | None]] = [("/", None), ("/map", None), ("/venues", None), ("/artists", None)]
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
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def robots(cfg: Settings) -> str:
    private = "Disallow: /admin\nDisallow: /admin/api/\n"
    ai = "".join(f"User-agent: {bot}\n" for bot in AI_CRAWLERS)
    return (
        f"# Search engines and AI assistants are welcome: the party listings are public.\n"
        f"User-agent: *\nAllow: /\n{private}\n{ai}Allow: /\n{private}\n"
        f"Sitemap: {cfg.site.base_url}/sitemap.xml\n"
    )


def llms_txt(session: Session, cfg: Settings) -> str:
    """https://llmstxt.org — a plain summary of the site and its live listings for AI assistants."""
    base = cfg.site.base_url
    rows = _upcoming(session, limit=LLMS_LIMIT)
    now = datetime.now(ZoneInfo(cfg.timezone))
    lines = [
        f"# {cfg.site.name}",
        "",
        "> Every party, club night and music event in Tel Aviv-Yafo, Israel, collected from Israeli "
        "ticketing sites and updated several times a day. For each event: date and time (Israel time, "
        "Asia/Jerusalem), venue and address, lineup / DJs, genres, minimum age, every ticket tier with "
        "its price in ILS (₪), free-entry flags, and a link to buy tickets on the original site.",
        "",
        f"Last updated: {now:%Y-%m-%d %H:%M} (Asia/Jerusalem). Site languages: Hebrew (default) and English (`?lang=en`).",
        "",
        "## Pages",
        f"- [Upcoming parties]({base}/): all upcoming events, filterable by date, genre, time of day and free entry",
        f"- [Map]({base}/map): events with a location",
        f"- [Venues]({base}/venues): every venue with upcoming events",
        f"- [Artists]({base}/artists): DJs and performers with upcoming shows",
        "",
        "## Data (JSON API, no key needed)",
        f"- [Events]({base}/events): filters `date=YYYY-MM-DD`, `weekend=true`, `genre`, `artist`, `venue`, "
        "`free_entry=true`, `time_slot` (morning/afternoon/evening/night), `area` (south, rothschild, center, business, "
        "beach, north, jaffa, outside), `min_price`, `max_price`, `q`; paged with `page`, `page_size`",
        f"- [Event detail]({base}/events/{{id}}): tickets, prices, fees, images",
        f"- [Categories]({base}/categories): counts per genre, date, venue, artist",
        f"- [OpenAPI schema]({base}/openapi.json)",
        "",
        f"## Upcoming parties (next {len(rows)})",
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

    index = site_dir / "index.html"
    if not index.is_file():
        return

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

    @app.get("/map", include_in_schema=False)
    def map_(request: Request) -> HTMLResponse:
        return page(request, map_page)

    @app.get("/admin", include_in_schema=False)
    def admin(request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: Page("Admin", "", "/admin", lang, noindex=True))
