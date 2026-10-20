"""Mevalim (mevalim.co.il) - show listings for a region (theatre, stand-up, concerts...).

The region page (e.g. ``/central/``) holds a table of upcoming performances fed by
``admin-ajax.php`` (``action=mevalim_load_more_events``, 20 rows per page, 1-based
``page``). The request needs the ``mevalimAjax.nonce`` printed on the region page.
Each row carries everything we keep: performance id, title, date+time, hall, city,
category, producer, deal + original price (base64 JSON in ``data-item``) and the
purchase link (``#updates`` when sold out). No detail requests are made (no artwork).
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_NONCE = re.compile(r'var mevalimAjax\s*=\s*\{[^}]*"nonce"\s*:\s*"([^"]+)"')
_PRICE = re.compile(r"\d+(?:\.\d+)?")


class MevalimSettings(SourceSettings):
    site_url: str
    region_path: str
    ajax_path: str
    ajax_action: str
    table_id: int
    page_size: int = Field(20, ge=1)
    max_pages: int = Field(100, ge=1)


class MevalimScraper(BaseScraper[MevalimSettings]):
    name = "mevalim"
    settings_model = MevalimSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        match = _NONCE.search(await self.get_text(s.site_url + s.region_path))
        if not match:
            raise ParseError("mevalimAjax nonce not found on region page")
        nonce = match.group(1)

        async def page(index: int) -> tuple[list[LexborNode], None]:
            payload = await self.post_json(
                s.site_url + s.ajax_path,
                data={
                    "action": s.ajax_action,
                    "nonce": nonce,
                    "is_passed": 0,
                    "post_id": s.table_id,
                    "page": index + 1,
                    "source_type": "mevalim",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                expect=dict,
            )
            if not payload.get("success"):
                raise ParseError(f"load more refused: {payload.get('data')}")
            return parse_rows(str((payload.get("data") or {}).get("content") or "")), None

        rows = await self.paginate(page, s.page_size, s.max_pages)
        events: dict[str, ScrapedEvent] = {}
        for row in rows:
            try:
                event = parse_row(row, s.site_url + s.region_path, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row never drops the listing
                self.record_failure(row.attributes.get("data-mvlm-event-id") or "row", exc)
                continue
            events[event.source_event_id] = event  # pages overlap by a row or two
        self.log.info("listed %d events", len(events))
        for event in events.values():
            yield event


def parse_rows(fragment: str) -> list[LexborNode]:
    # Bare <tr> outside a table is dropped by the HTML parser.
    return LexborHTMLParser(f"<table><tbody>{fragment}</tbody></table>").css("tr[data-mvlm-event-id]")


def parse_row(row: LexborNode, region_url: str, timezone: str) -> ScrapedEvent:
    attrs = row.attributes
    event_id = clean_str(attrs.get("data-mvlm-event-id"))
    if not event_id:
        raise ParseError("event id missing")
    link = row.css_first("a[href]")
    href = (link.attributes.get("href") or "") if link else ""
    sold_out = link is not None and "link-sold" in (link.attributes.get("class") or "")
    item = _item(attrs.get("data-item"))
    price = to_decimal(item.get("discount")) or _price_text(row)
    original = to_decimal(item.get("price"))
    categories = [c for c in (clean_str(x) for x in (attrs.get("data-mvlm-event-category") or "").split(",")) if c]
    # Drop tracking parameters; sold-out rows have no purchase link, so they point at the region page.
    url = urlunsplit(urlsplit(href)._replace(query="", fragment="")) if href.startswith("http") else region_url
    return ScrapedEvent(
        source=MevalimScraper.name,
        source_event_id=event_id,
        url=url,
        title=clean_str(attrs.get("data-mvlm-event-name")) or "",
        start_at=_parse_start(attrs.get("data-mvlm-event-date"), timezone),
        venue_name=_text(row, ".rgbcode_table_shortcode_table_hall"),
        city=_text(row, ".rgbcode_table_shortcode_table_city"),
        event_types=categories,
        organizer=clean_str(attrs.get("data-mvlm-brand")),
        min_price=price,
        max_price=price,
        is_sold_out=sold_out,
        tickets=(
            [TicketTier(name="כרטיס", price=price, is_free=price == 0, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
            if price is not None
            else []
        ),
        extra={
            "original_price": float(original) if original is not None and original != price else None,
            "ticket_site": clean_str(attrs.get("data-mvlm-event-domain")),
        },
    )


def _item(raw: str | None) -> dict[str, Any]:
    """Analytics payload: base64 JSON with ``price`` (original) and ``discount`` (deal price)."""
    try:
        data = json.loads(base64.b64decode(raw or ""))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _price_text(row: LexborNode) -> Any:
    match = _PRICE.search(_text(row, ".rgbcode_table_shortcode_table_link_price") or "")
    return to_decimal(match.group(0)) if match else None


def _text(row: LexborNode, selector: str) -> str | None:
    node = row.css_first(selector)
    return clean_str(node.text()) if node else None


def _parse_start(value: Any, timezone: str) -> datetime | None:
    """Local ``dd.mm.yy HH:MM``."""
    text = clean_str(value)
    if not text:
        return None
    return datetime.strptime(text, "%d.%m.%y %H:%M").replace(tzinfo=ZoneInfo(timezone))
