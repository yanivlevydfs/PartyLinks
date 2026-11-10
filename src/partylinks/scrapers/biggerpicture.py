"""BiggerPicture ecom ticketing (ecom.biggerpicture.ai) - ticket prices of HOT Cinema and Movieland.

Both chains sell through BiggerPicture's Angular shop (``<shop>/site/<siteId>?code=<siteId>-<eventId>``),
whose public API needs an anonymous session per branch site:

* ``POST /sys/login`` with ``{siteId, saleChannelCode, language}`` -> ``{token}`` (a JWT, sent as a
  Bearer header); a token of one site sees no event of another;
* ``GET /cus/event/code/<code>/min`` -> the screening with its seat sections (``vsp.vsedl``), each
  listing its ticket types (``tged.tedl``): name ``n``, online price ``pp`` with the online fee
  ``sca`` in it, and the ticket type ``tti`` (1 = regular; 90 = concessions that need an ID at the
  box office, left out).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from partylinks.models import TicketTier
from partylinks.parsing import clean_str, to_decimal
from partylinks.scrapers.base import BaseScraper

REGULAR = 1  # tti of the full-price ticket


class BiggerPicture:
    """Token-per-site client over a scraper's own HTTP client (proxy, retries, concurrency)."""

    def __init__(self, scraper: BaseScraper[Any], api_url: str, sale_channel: str, language: str) -> None:
        self.scraper, self.api_url, self.sale_channel, self.language = scraper, api_url, sale_channel, language
        self._tokens: dict[int, asyncio.Task[str]] = {}

    async def _login(self, site_id: int) -> str:
        body = {"siteId": site_id, "saleChannelCode": self.sale_channel, "language": self.language}
        payload = await self.scraper.post_json(self.api_url + "/sys/login", json=body, expect=dict)
        if not (token := clean_str(payload.get("token"))):
            raise ValueError(f"site {site_id}: login without token")
        return token

    async def tiers(self, site_id: int, event_id: Any) -> list[TicketTier]:
        """Regular tickets of one screening, one per seat section price."""
        if site_id not in self._tokens:  # one login per site, shared by the concurrent requests
            self._tokens[site_id] = asyncio.ensure_future(self._login(site_id))
        token = await asyncio.shield(self._tokens[site_id])
        payload = await self.scraper.get_json(
            f"{self.api_url}/cus/event/code/{site_id}-{event_id}/min",
            headers={"Authorization": f"Bearer {token}"},
            expect=dict,
        )
        return parse_event_tiers(payload)


def parse_event_tiers(payload: dict[str, Any]) -> list[TicketTier]:
    events = payload.get("list") or []
    sections = ((events[0].get("vsp") or {}).get("vsedl") or []) if events and isinstance(events[0], dict) else []
    tiers: list[TicketTier] = []
    for section in sections:
        for ticket in ((section or {}).get("tged") or {}).get("tedl") or []:
            paid, fee = to_decimal(ticket.get("pp")), to_decimal(ticket.get("sca")) or Decimal(0)
            if ticket.get("tti") != REGULAR or paid is None:
                continue
            name = clean_str(ticket.get("n")) or "רגיל"
            if len(sections) > 1 and (where := clean_str(section.get("dn"))):
                name = f"{name} - {where}"
            tier = TicketTier(name=name, price=paid - fee, service_fee=fee or None, is_free=paid == 0)
            if all((t.name, t.price) != (tier.name, tier.price) for t in tiers):
                tiers.append(tier)
    return tiers
