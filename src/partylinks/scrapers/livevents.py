"""Live Events (livevents.co.il) - packages (flight + hotel + ticket) and tickets to shows in Europe.

The site's "shows calendar" (``/events/``) loads one table per month from WordPress
``admin-ajax.php`` (``action=events_table_action&month=MM.YYYY``, POST, the same call the
page makes): each row is an artist, a city (Hebrew, no country) and one or more dates,
with a link and "from" price when a package or ticket offer exists, else a "request a
quote" button. One ``ScrapedEvent`` per row and date; ``months_ahead`` requests per run.
Rows without an offer link point to the calendar page. Times are not given (date only).
robots.txt is absent (404).
"""

from __future__ import annotations

import hashlib
import html as htmllib
import re
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import Field

from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_decimal, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD", "₪": "ILS"}
# The calendar's cities (Hebrew) -> ISO country; others get UNKNOWN_COUNTRY.
CITY_COUNTRY: dict[str, str] = {
    **dict.fromkeys(
        ["לונדון", "מנצ'סטר", "ברמינגהאם", "גלאזגו", "ליברפול", "קרדיף", "לידס", "שפילד", "נוטינגהאם", "אדינבורו"], "GB"
    ),
    "דבלין": "IE",
    **dict.fromkeys(["פריז", "ליון", "מרסיי", "ניס", "ליל"], "FR"),
    **dict.fromkeys(
        [
            "ברלין",
            "מינכן",
            "קלן",
            "האנובר",
            "פרנקפורט",
            "המבורג",
            "שטוטגרט",
            "דיסלדורף",
            "לייפציג",
            "נירנברג",
            "גלזנקירכן",
        ],
        "DE",
    ),
    **dict.fromkeys(["אמסטרדם", "רוטרדם", "מאסטריכט", "אוטרכט", "ארנהם", "ניימכן"], "NL"),
    **dict.fromkeys(
        ["מילאנו", "רומא", "ורונה", "פירנצה", "נאפולי", "טורינו", "בולוניה", "לאג'טיקו", "פומפיי", "לוקה"], "IT"
    ),
    **dict.fromkeys(["מדריד", "ברצלונה", "ולנסיה", "סביליה"], "ES"),
    **dict.fromkeys(["ליסבון", "פורטו"], "PT"),
    **dict.fromkeys(["בריסל", "אנטוורפן", "גנט"], "BE"),
    **dict.fromkeys(["ציריך", "באזל", "ז'נבה", "ברן"], "CH"),
    **dict.fromkeys(["וינה", "זלצבורג", "גראץ"], "AT"),
    "פראג": "CZ",
    **dict.fromkeys(["קראקוב", "ורשה", "לודז'", "גדאנסק", "קטוביץ", "פוזנן", "ורוצלב"], "PL"),
    "בודפשט": "HU",
    "ברטיסלבה": "SK",
    "לובליאנה": "SI",
    "זאגרב": "HR",
    "בלגרד": "RS",
    "סופיה": "BG",
    "בוקרשט": "RO",
    "אתונה": "GR",
    "סלוניקי": "GR",
    "ריגה": "LV",
    "וילנה": "LT",
    "טאלין": "EE",
    "קופנהאגן": "DK",
    "שטוקהולם": "SE",
    "אוסלו": "NO",
    "הלסינקי": "FI",
    "אבו דאבי": "AE",
    "דובאי": "AE",
}

_ROW = re.compile(r'<div class="line[^"]*">(.*?)<div class="td link-rap">(.*?)</div></div>', re.DOTALL)
_CELL = re.compile(r'<div class="td (\w+)">(.*?)</div>', re.DOTALL)
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_LINK = re.compile(r'href="([^"]+)"')
_PRICE = re.compile(r'class="p">([^<]*)</span><span class="c">([^<]*)</span>')


class LiveventsSettings(SourceSettings):
    site_url: str
    ajax_path: str  # admin-ajax.php
    ajax_action: str  # the calendar's month table
    calendar_path: str  # the calendar page (link of rows without an offer)
    months_ahead: int = Field(24, ge=1)


class LiveventsScraper(BaseScraper[LiveventsSettings]):
    name = "livevents"
    settings_model = LiveventsSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone))
        months = [
            f"{(today.month - 1 + i) % 12 + 1:02d}.{today.year + (today.month - 1 + i) // 12}"
            for i in range(s.months_ahead)
        ]

        async def month_table(month: str) -> list[ScrapedEvent]:
            data = {"action": s.ajax_action, "month": month, "count": 10}
            table = (await self.request("POST", s.site_url + s.ajax_path, data=data)).text
            return parse_table(table, s.site_url + s.calendar_path, self.timezone)

        seen: set[str] = set()  # a row spanning two months is in both tables
        async for events in self.stream_isolated(months, month_table, label=lambda m: f"month {m}"):
            for event in events:
                if event.source_event_id not in seen:
                    seen.add(event.source_event_id)
                    yield event


def _text(fragment: str) -> str | None:
    return clean_str(re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))))


def parse_table(table_html: str, calendar_url: str, timezone: str) -> list[ScrapedEvent]:
    """One event per row and date of a month's table."""
    events = []
    for cells_html, link_html in _ROW.findall(table_html):
        cells = {name: value for name, value in _CELL.findall(cells_html)}
        artist, city = _text(cells.get("artist", "")), _text(cells.get("place", ""))
        if not artist:
            continue
        status = _text(cells.get("status", ""))
        link = _LINK.search(link_html)
        price = _PRICE.search(link_html)
        for day, month, year in _DATE.findall(cells.get("date", "")):
            key = f"{artist}|{city}|{year}-{month}-{day}"
            events.append(
                ScrapedEvent(
                    source=LiveventsScraper.name,
                    source_event_id=hashlib.sha1(key.encode()).hexdigest()[:16],
                    url=link.group(1) if link else calendar_url,
                    title=artist,
                    start_at=datetime(int(year), int(month), int(day), tzinfo=zone(timezone)),  # date only
                    city=city,
                    country=CITY_COUNTRY.get(city or "", UNKNOWN_COUNTRY),
                    artists=[artist],
                    currency=CURRENCY_SIGNS.get(price.group(2).strip(), "EUR") if price else "EUR",
                    min_price=to_decimal(price.group(1).replace(",", "")) if price else None,
                    extra={
                        k: v
                        for k, v in {"status": status, "package": bool(link and "/package/" in link.group(1))}.items()
                        if v
                    },
                )
            )
    return events
