"""Blog / magazine: evergreen guides that rank for what people search ("מסיבות חנוכה", "חיי לילה בתל
אביב") and keep themselves fresh with live event lists from the listings.

Posts are files in ``content/blog/<slug>.html``: a YAML header between ``---`` lines, then an HTML
body. A ``<!--events genre=techno when=weekend city=tel-aviv limit=6-->`` comment in the body is a
live list of matching upcoming events (venue=, artist=, free=true also work). Posts dated in the
future or marked ``draft: true`` stay hidden until their date.

Served three ways: server-rendered pages for crawlers (``/blog``, ``/blog/category/<key>``,
``/blog/<slug>``), JSON for the React site (``/posts``, ``/posts/<slug>``) and an RSS feed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from email.utils import format_datetime
from html import escape, unescape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import ColumnElement, String, cast

from partylinks import seo
from partylinks.config import Settings
from partylinks.db import EventRow

log = logging.getLogger(__name__)

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "blog"
CATEGORIES = {  # key -> (Hebrew, English)
    "guides": ("מדריכים", "Guides"),
    "holidays": ("חגים ומועדים", "Holidays"),
    "cities": ("ערים ושכונות", "Cities"),
    "music": ("מוזיקה וז׳אנרים", "Music"),
    "tips": ("טיפים", "Tips"),
}
EMBED = re.compile(r"<!--\s*events\b(.*?)-->", re.S)
H2 = re.compile(r"<h2>(.*?)</h2>", re.S)
EMBED_MAX = 24
EMBED_DEFAULT = 6
WORDS_PER_MINUTE = 200
RELATED = 3


class Faq(BaseModel):
    q: str
    a: str


class Post(BaseModel):
    slug: str
    title: str
    description: str
    date: date
    updated: date | None = None
    category: str = "guides"
    image: str | None = None
    author: str | None = None
    draft: bool = False
    faq: list[Faq] = []
    body: str = ""

    @property
    def modified(self) -> date:
        return self.updated or self.date

    @property
    def read_minutes(self) -> int:
        words = len(re.sub(r"<[^>]+>", " ", EMBED.sub(" ", self.body)).split())
        return max(1, round(words / WORDS_PER_MINUTE))


# --------------------------------------------------------------------------- loading
def _parse(path: Path) -> Post | None:
    text = path.read_text(encoding="utf-8")
    _, header, body = text.split("---", 2) if text.startswith("---") else ("", "", text)
    try:
        post = Post(slug=path.stem, body=body.strip(), **(yaml.safe_load(header) or {}))
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        log.warning("blog post %s skipped: %s", path.name, exc)
        return None
    if post.category not in CATEGORIES:
        log.warning("blog post %s: unknown category %r", path.name, post.category)
        post.category = "guides"
    post.body = _anchor_headings(post.body)
    return post


def load_posts(cfg: Settings, content: Path | None = None) -> list[Post]:
    """Published posts, newest first. ponytail: re-read from disk per request; cache by mtime if
    the blog grows past a few hundred posts."""
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    posts = [p for p in map(_parse, sorted((content or CONTENT_DIR).glob("*.html"))) if p and not p.draft and p.date <= today]
    return sorted(posts, key=lambda p: (p.date, p.slug), reverse=True)


def _anchor_id(text: str) -> str:
    return re.sub(r"[^\w]+", "-", unescape(re.sub(r"<[^>]+>", "", text)).strip().lower()).strip("-")


def _anchor_headings(body: str) -> str:
    """<h2> gets an id, so the table of contents (and search sitelinks) can jump to it."""
    return H2.sub(lambda m: f'<h2 id="{escape(_anchor_id(m[1]))}">{m[1]}</h2>', body)


def toc(post: Post) -> list[dict[str, str]]:
    return [{"id": m[1], "title": unescape(re.sub(r"<[^>]+>", "", m[2]))}
            for m in re.finditer(r'<h2 id="([^"]*)">(.*?)</h2>', post.body, re.S)]  # fmt: skip


# --------------------------------------------------------------------------- live event embeds
def embed_params(raw: str, cfg: Settings) -> dict[str, str]:
    """Embed attributes -> the same query the React grid sends to /events."""
    args = {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', raw)}
    limit = args.get("limit", "")
    params = {"page_size": str(min(int(limit), EMBED_MAX) if limit.isdigit() else EMBED_DEFAULT)}
    if "genre" in args:
        params["genre"] = args["genre"]
    if args.get("when") == "tonight":
        params["date"] = datetime.now(ZoneInfo(cfg.timezone)).date().isoformat()
    elif args.get("when") == "weekend":
        params["weekend"] = "true"
    city = next((c for c in cfg.enrichment.cities if c.key == args.get("city")), None)
    if city:
        params["city"] = city.he
    for key in ("venue", "artist"):
        if args.get(key):
            params[key] = args[key]
    if args.get("free") == "true":
        params["free_entry"] = "true"
    return params


def _embed_rows(session: Any, params: dict[str, str], cfg: Settings) -> list[EventRow]:
    where: list[ColumnElement[bool]] = []
    if "genre" in params:
        where.append(seo._genre_filter(params["genre"], cfg))
    if "date" in params:
        where += seo._when_where("tonight", cfg)
    if "weekend" in params:
        where += seo._when_where("weekend", cfg)
    if "city" in params:
        where.append(EventRow.city == params["city"])
    if "venue" in params:
        where.append(EventRow.venue_name.ilike(f"%{params['venue']}%"))
    if "artist" in params:
        where.append(cast(EventRow.lineup, String).ilike(f"%{params['artist']}%"))
    if "free_entry" in params:
        where.append(EventRow.has_free_entry.is_(True))
    return seo._upcoming(session, *where, limit=int(params["page_size"]))


def blocks(post: Post, cfg: Settings) -> list[dict[str, Any]]:
    """The body split into HTML chunks and live event lists, for the React page."""
    out: list[dict[str, Any]] = []
    last = 0
    for m in EMBED.finditer(post.body):
        out.append({"html": post.body[last : m.start()]})
        out.append({"events": embed_params(m[1], cfg)})
        last = m.end()
    out.append({"html": post.body[last:]})
    return [b for b in out if b.get("events") or b.get("html", "").strip()]


def _image(session: Any, post: Post, cfg: Settings) -> str | None:
    """The post's own image, else the artwork of the first event in its first live list."""
    if post.image:
        return post.image if post.image.startswith("http") else cfg.site.base_url + post.image
    first = EMBED.search(post.body)
    rows = _embed_rows(session, embed_params(first[1], cfg) | {"page_size": "1"}, cfg) if first else []
    return seo._cover(cfg, rows[0]) if rows else None


# --------------------------------------------------------------------------- JSON for the site
def _cat(key: str) -> dict[str, str]:
    he, en = CATEGORIES[key]
    return {"key": key, "he": he, "en": en}


def summary(session: Any, post: Post, cfg: Settings) -> dict[str, Any]:
    return {
        "slug": post.slug, "title": post.title, "description": post.description, "date": post.date,
        "updated": post.modified, "category": _cat(post.category), "image": _image(session, post, cfg),
        "read_minutes": post.read_minutes,
    }  # fmt: skip


def related(posts: list[Post], post: Post) -> list[Post]:
    """Same category first, then the newest of the rest."""
    others = [p for p in posts if p.slug != post.slug]
    return sorted(others, key=lambda p: p.category != post.category)[:RELATED]


# --------------------------------------------------------------------------- server-rendered pages
def _path(post: Post) -> str:
    return f"/blog/{post.slug}"


def _posts_html(posts: list[Post]) -> str:
    return "<ul>" + "".join(
        f'<li><a href="{_path(p)}">{escape(p.title)}</a> · {p.date:%d/%m/%Y} · {escape(p.description)}</li>' for p in posts
    ) + "</ul>"


def _blog_ld(cfg: Settings, posts: list[Post], name: str, path: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": name,
        "url": seo._url(cfg, path),
        "inLanguage": "he",
        "publisher": {"@id": cfg.site.base_url + "/#org"},
        "blogPost": [
            {"@type": "BlogPosting", "headline": p.title, "url": seo._url(cfg, _path(p)), "datePublished": p.date.isoformat()}
            for p in posts
        ],
    }


def _title(lang: str) -> str:
    return "המגזין של דופק: מדריכים לחיי הלילה" if lang == "he" else "Pulse magazine: nightlife guides"


def _desc(lang: str) -> str:
    return (
        "מדריכים, טיפים והמלצות לחיי הלילה בישראל: מסיבות חגים, שכונות בילוי, ז׳אנרים וחיסכון בכרטיסים, עם אירועים שמתעדכנים כל הזמן."
        if lang == "he"
        else "Guides and tips for nightlife in Israel: holiday parties, neighborhoods, genres and saving on tickets, with live event lists."
    )


def index_page(session: Any, cfg: Settings, lang: str, category: str | None = None) -> seo.Page | None:
    posts = load_posts(cfg)
    title, path = _title(lang), "/blog"
    crumbs = [(title, path)]
    if category:
        if category not in CATEGORIES:
            return None
        posts = [p for p in posts if p.category == category]
        name = CATEGORIES[category][0 if lang == "he" else 1]
        title, path = f"{name} · {title}", f"/blog/category/{category}"
        crumbs.append((name, path))
    cats = "".join(f'<li><a href="/blog/category/{k}">{escape(v[0 if lang == "he" else 1])}</a></li>' for k, v in CATEGORIES.items())
    return seo.Page(
        title=f"{title} · {cfg.site.name}",
        description=_desc(lang),
        path=path,
        lang=lang,
        langs=("he",),
        jsonld=[_blog_ld(cfg, posts, title, path), seo._breadcrumbs(cfg, "he", *crumbs)],
        body=f"<h1>{escape(title)}</h1><p>{escape(_desc(lang))}</p><nav><ul>{cats}</ul></nav>" + _posts_html(posts),
        noindex=not posts,
    )


def post_page(session: Any, cfg: Settings, lang: str, slug: str) -> seo.Page | None:
    posts = load_posts(cfg)
    post = next((p for p in posts if p.slug == slug), None)
    if post is None:
        return None
    path, url = _path(post), seo._url(cfg, _path(post))
    image = _image(session, post, cfg)
    category = CATEGORIES[post.category][0]
    body = post.body
    embedded: list[EventRow] = []
    for m in reversed(list(EMBED.finditer(body))):
        rows = _embed_rows(session, embed_params(m[1], cfg), cfg)
        embedded = rows + embedded
        body = body[: m.start()] + seo._event_list_html(rows, cfg, "he") + body[m.end() :]
    article = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "@id": url + "#article",
        "headline": post.title,
        "description": post.description,
        "url": url,
        "mainEntityOfPage": url,
        "datePublished": post.date.isoformat(),
        "dateModified": post.modified.isoformat(),
        "inLanguage": "he",
        "articleSection": category,
        "wordCount": post.read_minutes * WORDS_PER_MINUTE,
        "image": [image] if image else None,
        "author": {"@type": "Organization", "name": post.author or cfg.site.name, "url": cfg.site.base_url + "/"},
        "publisher": {"@id": cfg.site.base_url + "/#org"},
    }
    jsonld: list[dict[str, Any]] = [
        {k: v for k, v in article.items() if v is not None},
        seo._breadcrumbs(cfg, "he", (_title("he"), "/blog"), (category, f"/blog/category/{post.category}"), (post.title, path)),
    ]
    if embedded:
        jsonld.append(seo._item_list(embedded[: seo.LIST_LIMIT], cfg, post.title))
    faq_html = ""
    if post.faq:
        jsonld.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": f.q, "acceptedAnswer": {"@type": "Answer", "text": f.a}} for f in post.faq],
        })  # fmt: skip
        faq_html = "<section><h2>שאלות נפוצות</h2>" + "".join(f"<h3>{escape(f.q)}</h3><p>{escape(f.a)}</p>" for f in post.faq) + "</section>"
    more = related(posts, post)
    return seo.Page(
        title=f"{post.title} · {cfg.site.name}",
        description=post.description,
        path=path,
        lang=lang,
        langs=("he",),
        image=image,
        og_type="article",
        jsonld=jsonld,
        body=(
            f'<article lang="he" dir="rtl"><h1>{escape(post.title)}</h1>'
            f'<p><a href="/blog/category/{post.category}">{escape(category)}</a> · '
            f'<time datetime="{post.date.isoformat()}">{post.date:%d/%m/%Y}</time> · {post.read_minutes} דקות קריאה</p>'
            f"{body}{faq_html}</article>"
            + (f"<aside><h2>עוד במגזין</h2>{_posts_html(more)}</aside>" if more else "")
        ),
    )


def rss(cfg: Settings) -> str:
    def item(p: Post) -> str:
        when = datetime.combine(p.date, datetime.min.time(), ZoneInfo(cfg.timezone))
        url = escape(seo._url(cfg, _path(p)))
        return (f"<item><title>{escape(p.title)}</title><link>{url}</link><guid>{url}</guid>"
                f"<pubDate>{format_datetime(when)}</pubDate><category>{escape(CATEGORIES[p.category][0])}</category>"
                f"<description>{escape(p.description)}</description></item>")  # fmt: skip

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{escape(_title('he'))}</title><link>{cfg.site.base_url}/blog</link>"
        f"<description>{escape(_desc('he'))}</description><language>he</language>"
        + "".join(item(p) for p in load_posts(cfg))
        + "</channel></rss>\n"
    )


# --------------------------------------------------------------------------- routes
def register(app: FastAPI, page: Any) -> None:
    """``page(request, build)`` is seo's server-render helper (None when the site isn't built)."""

    def cfg(request: Request) -> Settings:
        return request.app.state.settings

    @app.get("/posts", tags=["blog"])
    def list_posts(request: Request, category: str | None = None) -> dict[str, Any]:
        posts = load_posts(cfg(request))
        counts = {k: sum(p.category == k for p in posts) for k in CATEGORIES}
        shown = [p for p in posts if not category or p.category == category]
        with request.app.state.db.session() as session:
            return {
                "categories": [_cat(k) | {"posts": n} for k, n in counts.items() if n],
                "items": [summary(session, p, cfg(request)) for p in shown],
            }

    @app.get("/posts/{slug}", tags=["blog"])
    def get_post(slug: str, request: Request) -> dict[str, Any]:
        posts = load_posts(cfg(request))
        post = next((p for p in posts if p.slug == slug), None)
        if post is None:
            raise HTTPException(404, "post not found")
        with request.app.state.db.session() as session:
            return summary(session, post, cfg(request)) | {
                "blocks": blocks(post, cfg(request)),
                "toc": toc(post),
                "faq": [f.model_dump() for f in post.faq],
                "related": [summary(session, p, cfg(request)) for p in related(posts, post)],
            }

    @app.get("/blog/rss.xml", include_in_schema=False)
    def feed(request: Request) -> Response:
        return Response(rss(cfg(request)), media_type="application/rss+xml")

    if page is None:
        return

    @app.get("/blog", include_in_schema=False)
    def blog_index(request: Request) -> Response:
        return page(request, index_page)

    @app.get("/blog/category/{key}", include_in_schema=False)
    def blog_category(key: str, request: Request) -> Response:
        return page(request, lambda s, c, lang: index_page(s, c, lang, key))

    @app.get("/blog/{slug}", include_in_schema=False)
    def blog_post(slug: str, request: Request) -> Response:
        return page(request, lambda s, c, lang: post_page(s, c, lang, slug))
