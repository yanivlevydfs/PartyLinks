"""Tel Aviv-Yafo municipality (tel-aviv.gov.il/Visitors/Events) - the city's own events listing.

Culture, community centres, tours, lectures, sport, kids' activities - many free, some paid
through a seller. SharePoint; the page's Angular widget (EventsAndBenefitsLobby.js) reads every
event with one POST to the site's own service
``/_vti_bin/TlvSP2013PublicSite/TlvListUtils.svc/GetEvBenList`` (no paging: ~1000 items, ~9 MB).
Each item is a list of ``{InternalName, Value}`` fields: title, summary, full text (``Comments``,
plain text), start / end ("17.10.26, 10:00", local), place name, street address, picture
(``<img>``), fields of interest, the organising centre, paid / free ("בתשלום" / "לא בתשלום" /
"חלק בתשלום") and the cost as free text ("35-60 ש\"ח"). A recurring item gives its next
occurrence; a run (exhibition, festival) its first and last day, so a started one stays current.
Seller links (Eventer, GoShow, Coing...) sit in the text and are kept as ``extra["purchase_url"]``.
Coordinates are on the Israeli grid (ITM) and are not used: the address is geocoded.
robots.txt disallows /Lists/, /Search/ and a few asset paths, not /_vti_bin/.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_IMG = re.compile(r'src="([^"]+)"')
_NUMBER = re.compile(r"\d+")
# Links to a ticket seller in the item's text (bit.ly and social links are not followed).
_SELLER = re.compile(
    r"https?://(?:www\.)?(?:eventer\.co\.il|goshow\.co\.il/show|coing\.co|tickchak\.co\.il|smarticket\.co\.il)/[^\s\"<>]+"
)
# Place names that only point to the text.
_NO_PLACE = {"כמפורט בכתבה", "כמפורט בידיעה", "כמפורט באירוע"}
FREE, PARTLY_PAID = "לא בתשלום", "חלק בתשלום"


class TelAvivSettings(SourceSettings):
    site_url: str
    list_path: str  # /_vti_bin/TlvSP2013PublicSite/TlvListUtils.svc/GetEvBenList
    item_path: str  # /Pages/MainItemPage.aspx
    city: str
    # The search window the page itself uses: today and the next year.
    days_ahead: int = Field(365, ge=1)
    max_items: int = Field(3000, ge=1)


class TelAvivScraper(BaseScraper[TelAvivSettings]):
    name = "telaviv"
    settings_model = TelAvivSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """The site's firewall answers a refused IP with its own 571 (seen via free proxies, Oct 2026)."""
        return response.status_code == 571

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Referer": self.settings.site_url + "/Visitors/Events/Pages/Events.aspx"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items = await self.post_json(s.site_url + s.list_path, json=search(s, self.timezone), expect=list)
        if len(items) >= s.max_items:
            self.log.warning("listing truncated at max_items=%d", s.max_items)
        events = parse_items(items, s, self.timezone)
        self.log.info("listed %d events", len(events))
        for event in events:
            yield event


def search(s: TelAvivSettings, timezone: str) -> dict[str, Any]:
    """The widget's search body with no filter set (as EventsAndBenefitsLobby.js sends it)."""
    today = datetime.now(zone(timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "srch_ctAll": ["סוג תוכן לאירועים", "סוג תוכן אירוע WITH"],
        "srch_orderByFields": ["TlvOrder", "TlvStartDate"],
        "srch_audID": "", "srch_locID": "", "srch_crmIntsID": [], "srch_toIntsID": [],
        "srch_digiOnly": False, "srch_free": False, "srch_tickets": False,
        "srch_dateStart": _utc(today),
        "srch_dateEnd": _utc(today + timedelta(days=s.days_ahead, hours=23, minutes=59)),
        "srch_txt": "", "srch_Omp": "", "srch_radius": 500, "srch_myLat": 0, "srch_myLon": 0,
        "srch_iccID": "", "srch_AcsID": "", "srch_specialEvID": [],
        # Benefits (discounts for residents), not events.
        "srch_exceptions": ["סוג תוכן להטבות", "סוג תוכן הטבות עם רישום", "סוג תוכן הטבות עם בתשלום", "סוג תוכן הטבות עסקיות", "TlvInterestsID:x17o55x"],
        "srch_totalItemsLoad": s.max_items,
        "srch_paramsStrForCache": "VisitorsEventsPagesEvents.aspxIccID-1DOfalseFreefalseDtRng-1eab",
        "srch_showBoundBenefits": False, "srch_showInLobby": False, "srch_delivery": False,
        "srch_morning": False, "srch_noon": False, "srch_evening": False,
        "srch_lstTktCtgr": "1,8", "srch_isBusiness": False, "srch_bsCtgry": [], "srch_BenChldDS": None,
    }  # fmt: skip


def _utc(moment: datetime) -> str:
    """How the widget sends dates (moment.js JSON): UTC with milliseconds."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_items(items: list[Any], s: TelAvivSettings, timezone: str) -> list[ScrapedEvent]:
    events = []
    for raw in items:
        fields = raw.get("Fields") if isinstance(raw, dict) else None
        if isinstance(fields, list) and (event := parse_item(_values(fields), s, timezone)):
            events.append(event)
    return events


def _values(fields: list[Any]) -> dict[str, str]:
    return {f["InternalName"]: f.get("Value") or "" for f in fields if isinstance(f, dict) and f.get("InternalName")}


def _date(value: str, timezone: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%y, %H:%M").replace(tzinfo=zone(timezone))
    except ValueError:
        return None


def _prices(cost: str) -> list[Decimal]:
    """Shekel amounts of the free-text cost ("35-60 ש\"ח", "100/200 ש\"ח"); [] for "כמפורט באירוע"."""
    return sorted({Decimal(n) for n in _NUMBER.findall(cost)}) if 'ש"ח' in cost else []


def parse_item(v: dict[str, str], s: TelAvivSettings, timezone: str) -> ScrapedEvent | None:
    item_id, list_id, title = clean_str(v.get("ListItemID")), clean_str(v.get("ListID")), clean_str(v.get("Title"))
    start = _date(v.get("TlvStartDate", ""), timezone)
    if not item_id or not list_id or not title or not start:
        return None
    payment = clean_str(v.get("TlvPaymentRequired"))
    prices = _prices(v.get("TlvPaymentCost", ""))
    tickets = [TicketTier(name="כרטיס", price=p) for p in prices] if payment != FREE else []
    if payment in (FREE, PARTLY_PAID):
        tickets.insert(0, TicketTier(name="כניסה חופשית", price=0, is_free=True))
    image = _IMG.search(v.get("TlvMainPicture", ""))
    text = v.get("Comments", "")
    seller = _SELLER.search(text)
    place = clean_str(v.get("TlvCityLocation"))
    summary = clean_str(v.get("TlvSummary"))
    body = html_to_text(text)
    genres = (clean_str(g) for part in v.get("TlvFieldsOfInterests", "").split(";") for g in part.split("|"))
    return ScrapedEvent(
        source=TelAvivScraper.name,
        # Item ids repeat across the site's two event lists.
        source_event_id=f"{list_id[:8]}-{item_id}",
        url=f"{s.site_url}{s.item_path}?WebID={v.get('WebID', '')}&ListID={list_id}&ItemID={item_id}",
        title=title,
        description="\n\n".join(t for t in (summary, body) if t) or None,
        start_at=start,
        end_at=_date(v.get("TlvEndDate", ""), timezone),
        venue_name=None if place in _NO_PLACE else place,
        address=clean_str(v.get("TlvAddress1")),
        city=s.city,
        source_genres=list(dict.fromkeys(g for g in genres if g)),
        organizer=clean_str(v.get("TlvInchargeCenterFullVal")),
        images=[s.site_url + image.group(1) if image.group(1).startswith("/") else image.group(1)] if image else [],
        tickets=tickets,
        min_price=min(t.price for t in tickets if t.price is not None) if tickets else None,
        max_price=max(t.price for t in tickets if t.price is not None) if tickets else None,
        extra={
            **({"payment": payment} if payment else {}),
            **({"purchase_url": seller.group(0).rstrip(".,;)")} if seller else {}),
        },
    )
