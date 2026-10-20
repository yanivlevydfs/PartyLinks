"""Radio EPGB (epgb.co.il) - the club's own programme.

The site is static; its pages read the ``events`` table from the club's public
Supabase project with the anon key embedded in the page. The key and the project
URL are taken from the homepage on every run (so a key rotation needs no change);
the configured values are only a fallback when the page no longer carries them.
Only the public columns the site itself renders are selected - the table also
holds internal columns (attendance etc.) that are never requested. Every event is
at the club, so the venue comes from settings. Tickets are sold in an on-site
checkout (or an external link) whose prices are not in the table.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

# Public columns only (what the site's own pages read). Never add internal ones.
COLUMNS = (
    "id,title,title_en,date,start_time,open_time,genre,dj_name,artist_lineup,min_age,"
    "image_url,external_ticket_url,is_published,description,description_en,show_description"
)
_KEY = re.compile(r"""['"](eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)['"]""")
_PROJECT = re.compile(r"https://[a-z0-9]+\.supabase\.co")
_NAMES = re.compile(r"\s*(?:·|•|,|/|\||&|\bb2b\b)\s*", re.IGNORECASE)


class EpgbSettings(SourceSettings):
    site_url: str
    page_path: str
    event_page_path: str  # must contain "{id}"
    events_path: str  # Supabase REST path of the events table
    # Fallbacks, used only when the homepage no longer embeds them.
    supabase_url: str
    anon_key: str | None = None
    venue_name: str
    address: str
    city: str


class EpgbScraper(BaseScraper[EpgbSettings]):
    name = "epgb"
    settings_model = EpgbSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        project, key = await self._credentials()
        today = datetime.now(ZoneInfo(self.timezone)).date().isoformat()
        rows = await self.get_json(
            project + s.events_path,
            expect=list,
            params={
                "select": COLUMNS,
                "date": f"gte.{today}",
                "is_published": "eq.true",
                "order": "date.asc,start_time.asc",
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        self.log.info("listed %d events", len(rows))
        for row in rows:
            try:
                yield parse_event(row, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row never drops the listing
                self.record_failure(str(row.get("id") if isinstance(row, dict) else row)[:80], exc)

    async def _credentials(self) -> tuple[str, str]:
        s = self.settings
        page = await self.optional("homepage", self.get_text(s.site_url + s.page_path)) or ""
        key_match, project_match = _KEY.search(page), _PROJECT.search(page)
        key = key_match.group(1) if key_match else s.anon_key
        if not key:
            raise ParseError(f"{s.site_url}: no Supabase anon key on the page and none configured")
        if not key_match:
            self.log.warning("anon key not found on the page; using the configured one")
        return (project_match.group() if project_match else s.supabase_url), key


def parse_event(row: dict[str, Any], s: EpgbSettings, timezone: str) -> ScrapedEvent:
    event_id = clean_str(row.get("id"))
    if not event_id:
        raise ParseError("id missing")
    image = clean_str(row.get("image_url"))
    genre = clean_str(row.get("genre"))
    show_description = row.get("show_description") is not False
    return ScrapedEvent(
        source=EpgbScraper.name,
        source_event_id=event_id,
        url=clean_str(row.get("external_ticket_url")) or s.site_url + s.event_page_path.format(id=event_id),
        title=clean_str(row.get("title")) or clean_str(row.get("title_en")) or "",
        description=html_to_text(row.get("description")) if show_description else None,
        start_at=_parse_start(row.get("date"), row.get("start_time") or row.get("open_time"), timezone),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        source_genres=[g for g in _NAMES.split(genre) if g] if genre else [],
        artists=lineup(row),
        min_age=to_int(row.get("min_age")),
        organizer=s.venue_name,
        images=[image] if image else [],
        extra={
            "title_en": clean_str(row.get("title_en")),
            "doors": clean_str(row.get("open_time")),
            "external_tickets": bool(clean_str(row.get("external_ticket_url"))),
        },
    )


def lineup(row: dict[str, Any]) -> list[str]:
    """``artist_lineup`` when filled (list or text), else ``dj_name`` ("I-TEK · EDDIE BOMBO")."""
    raw = row.get("artist_lineup")
    if isinstance(raw, list):
        parts = [p.get("name") if isinstance(p, dict) else p for p in raw]
    else:
        parts = _NAMES.split(str(raw or row.get("dj_name") or ""))
    return list(dict.fromkeys(n for p in parts if (n := clean_str(p))))


def _parse_start(day: Any, time: Any, timezone: str) -> datetime | None:
    """Local ``yyyy-mm-dd`` + ``HH:MM[:SS]``."""
    day_s = clean_str(day)
    if not day_s:
        return None
    time_s = (clean_str(time) or "00:00")[:5]
    return datetime.strptime(f"{day_s} {time_s}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(timezone))
