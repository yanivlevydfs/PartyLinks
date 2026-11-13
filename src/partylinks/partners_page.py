"""/partners for crawlers: the same texts as the React page (web/src/partners/texts.json), as HTML."""

from __future__ import annotations

import json
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.seo import INFO_PAGES, Page, _breadcrumbs

TEXTS_FILE = Path(__file__).resolve().parents[2] / "web" / "src" / "partners" / "texts.json"


@cache
def _texts() -> dict[str, Any]:
    return json.loads(TEXTS_FILE.read_text(encoding="utf-8"))


def _section(s: dict[str, Any]) -> str:
    li = lambda items: "".join(f"<li>{escape(x)}</li>" for x in items)
    out = f"<h2>{escape(s['h'])}</h2>"
    out += f"<p>{escape(s['p'])}</p>" if s.get("p") else ""
    out += "".join(f"<p>{escape(v)} {escape(label)}</p>" for v, label in s.get("stats", []))
    out += f"<ul>{li(s['list'])}</ul>" if s.get("list") else ""
    out += f"<ol>{li(s['steps'])}</ol>" if s.get("steps") else ""
    out += "".join(f"<h3>{escape(h)}</h3><p>{escape(tag)}: {escape(p)}</p>" for h, tag, p in s.get("cards", []))
    out += f"<p>{escape(s['note'])}</p>" if s.get("note") else ""
    out += "".join(f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in s.get("faq", []))
    if link := s.get("link"):
        out += f'<p><a href="{escape(link[0])}">{escape(link[1])}</a></p>'
    return out  # no email: revealed on the page by a click


def partners_page(session: Session, cfg: Settings, lang: str) -> Page:
    t = _texts()[lang]
    h1 = INFO_PAGES["/partners"][0][0 if lang == "he" else 1]
    faq = next(s["faq"] for s in t["sections"] if s.get("faq"))
    return Page(
        title=f"{t['metaTitle']} · {cfg.site.name}",
        description=t["description"],
        path="/partners",
        lang=lang,
        jsonld=[
            _breadcrumbs(cfg, lang, (h1, "/partners")),
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq
                ],
            },
        ],
        body=f"<h1>{escape(h1)}</h1><p>{escape(t['eyebrow'])}</p><p>{escape(t['lead'])}</p>"
        + "".join(_section(s) for s in t["sections"]),
    )
