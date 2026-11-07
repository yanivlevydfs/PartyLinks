"""Municipal event lists on SharePoint (Rishon LeZion: rishonlezion.muni.il/RecreationalAndLeisure/Pages/Events.aspx).

The events page fills its search grid from one JSON call, ``<web>/_layouts/15/GetJsonItems.aspx?List=
<list id>&View=<view id>``, the city's whole coming programme (~90 for Rishon) with title, start / end
(``dd/mm/yyyy HH:MM:SS``, Israel wall clock), free / paid, event types, hall and picture. robots.txt
disallows the root ``/_layouts/`` only; this call is the sub-site's, the one the page itself makes.
Each row's ``CustomDispForm.aspx?ID=`` page (one request per event) adds the street address, the
summary and description, and the payment link.

Rows whose payment link goes to a box office another source reads (``skip_hosts``: Rishon's culture
hall htrl.co.il and Smarticket hosts) are skipped; what is left is free municipal events, matnasim,
the museum and other sellers, with their link kept as ``extra["purchase_url"]``.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class SharePointSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web_url: str  # https://www.rishonlezion.muni.il/RecreationalAndLeisure  (the sub-site holding the page)
    list_id: str
    view_id: str
    city: str
    organizer: str | None = None  # default "עיריית <city>"


class SharePointMuniSettings(SourceSettings):
    sites: list[SharePointSite] = Field(min_length=1)
    # Payment links to box offices read by other sources: those rows are their copies.
    skip_hosts: list[str] = Field(default_factory=list)
    skip_title_words: list[str] = Field(default_factory=list)
    free_words: list[str] = Field(default_factory=lambda: ["בחינם", "חינם", "ללא תשלום"])
    ignore_types: list[str] = Field(default_factory=lambda: ["אחר"])


class SharePointMuniScraper(BaseScraper[SharePointMuniSettings]):
    name = "sharepointmuni"
    settings_model = SharePointMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        for site in s.sites:
            url = f"{site.web_url}/_layouts/15/GetJsonItems.aspx?List={site.list_id}&View={site.view_id}"
            payload = await self.optional(f"{site.web_url} list", self.get_json(url, expect=dict))
            rows = [
                r
                for r in parse_list(payload or {}, self.timezone)
                if not _skipped_title(r["title"], s.skip_title_words)
            ]
            rows = [r for r in rows if is_current(r["start"], r["end"], retention, self.timezone)]

            async def fetch(row: dict[str, Any], site: SharePointSite = site) -> ScrapedEvent | None:
                page = await self.get_text(row["url"])
                return parse_event(row, parse_detail(page), site, s, self.timezone)

            async for event in self.stream_isolated(rows, fetch, label=lambda r: r["url"]):
                if event:
                    yield event


def _skipped_title(title: str, words: list[str]) -> bool:
    return any(w in title for w in words)


def _when(value: str | None, timezone: str) -> datetime | None:
    text = clean_str(value)
    if not text:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=zone(timezone))
        except ValueError:
            continue
    raise ParseError(f"date {text!r}")


def _https(url: str) -> str:
    parts = urlsplit(url.replace("http://", "https://", 1))
    return f"{parts.scheme}://{parts.netloc}{quote(parts.path)}" + (f"?{parts.query}" if parts.query else "")


def parse_list(payload: dict[str, Any], timezone: str = "Asia/Jerusalem") -> list[dict[str, Any]]:
    """The rows of ``GetJsonItems`` as plain dicts (title, url, start, end, cost, types, hall, image)."""
    items = (((payload.get("Root") or {}).get("Items") or {}).get("Item")) or []
    if isinstance(items, dict):  # one row comes as an object
        items = [items]
    out = []
    for item in items:
        fields = {f.get("Caption"): f for f in (item.get("Fields") or {}).get("Field") or []}

        def value(caption: str, fields: dict[str, Any] = fields) -> str | None:
            return clean_str(html.unescape((fields.get(caption) or {}).get("Value") or "")) or None

        title, link, start = (
            value("כותרת") or clean_str(item.get("Title")),
            clean_str(item.get("URL")),
            value("מועד התחלה"),
        )
        if not title or not link or not start:
            continue
        img = LexborHTMLParser(value("תמונה") or "<p></p>").css_first("img")
        src = img.attributes.get("src") if img else None
        out.append(
            {
                "id": str(item.get("ID")),
                "title": title,
                "url": _https(link),
                "start": _when(start, timezone),
                "end": _when(value("מועד סיום"), timezone),
                "cost": value("סוג עלות"),
                "types": [t.strip() for t in (value("סוג האירוע") or "").split(",") if t.strip()],
                "hall": value("מיקום לקישור"),
                "image": _https(src) if src and src.startswith("http") else None,
            }
        )
    return out


def parse_detail(page: str) -> dict[str, str]:
    """The display form's label -> value (text; links as their href under "<label>:href")."""
    tree = LexborHTMLParser(page)
    out: dict[str, str] = {}
    for row in tree.css("table.ms-formtable tr"):
        label, body = row.css_first("td.ms-formlabel"), row.css_first("td.ms-formbody")
        if not label or not body:
            continue
        key = clean_str(label.text())
        if not key:
            continue
        for hidden in body.css("[style*='display:none']"):  # the field's own label, repeated for screen readers
            hidden.decompose()
        out[key] = html_to_text(body.html or "") or ""
        if a := body.css_first("a[href]"):
            out[key + ":href"] = html.unescape(a.attributes.get("href") or "").replace("&#58;", ":")
    return out


def parse_event(
    row: dict[str, Any], detail: dict[str, str], site: SharePointSite, s: SharePointMuniSettings, timezone: str
) -> ScrapedEvent | None:
    """One event; None when its payment link is a box office another source reads."""
    link = clean_str(detail.get("קישור לתשלום:href"))
    host = (urlsplit(link).hostname or "") if link else ""
    if host and any(host == h or host.endswith("." + h) for h in s.skip_hosts):
        return None
    start, end = row["start"], row["end"]
    if end and end <= start:
        end = None
    free = row["cost"] in s.free_words
    summary, body = clean_str(detail.get("תקציר")), clean_str(detail.get("תיאור"))
    address = clean_str(detail.get("כתובת"))
    # No hall linked: the address usually starts with it ("מרכז נשים עירוני, רח' בן גוריון 78").
    head = address.split(",")[0].strip() if address and "," in address else ""
    venue = row["hall"] or (head if head and not any(c.isdigit() for c in head) else None)
    return ScrapedEvent(
        source=SharePointMuniScraper.name,
        source_event_id=f"{urlsplit(site.web_url).hostname}:{row['id']}",
        url=row["url"],
        title=row["title"],
        description="\n\n".join(t for t in (summary, body) if t) or None,
        start_at=start,
        end_at=end,
        time_known=True,
        venue_name=venue,
        address=address,
        city=site.city,
        source_genres=[t.lower() for t in row["types"] if t not in s.ignore_types],
        organizer=site.organizer or f"עיריית {site.city}",
        image_url=row["image"],
        min_price=Decimal(0) if free else None,
        max_price=Decimal(0) if free else None,
        tickets=[TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if free else [],
        extra={"purchase_url": link} if link and link.startswith("http") else {},
    )
