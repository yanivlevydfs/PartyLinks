"""UTM tags on outbound ticket links, so a seller's analytics (GA4 and others) can attribute
visits, clicks and sales to us, per event and per spot on our page.

Tags are added server-side in GET /go/... (the only way a ticket link leaves the server), so no
analytics cookies or scripts are needed on our side. Example result:

    https://seller.example/e/123?ref=x&utm_source=spotx&utm_medium=affiliate
        &utm_campaign=techno-night-2026-11-02&utm_content=buy&utm_term=<city>
"""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from partylinks.config import UtmSettings
from partylinks.db import EventRow

# Where on our site the click came from (?at= on /go/...): main button, another seller of the
# same event, the mobile sticky bar, the server-rendered page. Anything else is dropped.
PLACEMENTS = frozenset({"buy", "alt", "bar", "seo"})


def utm_params(cfg: UtmSettings, row: EventRow, placement: str | None = None) -> dict[str, str]:
    """The tags for one event. Empty values are left out (GA4 shows them as "(not set)")."""
    params = {
        "utm_source": cfg.source,
        "utm_medium": cfg.medium,
        "utm_campaign": row.slug or row.id,
        "utm_content": placement if placement in PLACEMENTS else "",
        "utm_term": row.city or "",
    }
    return {k: v for k, v in params.items() if v}


def tag_url(url: str, params: dict[str, str]) -> str:
    """``url`` with ``params`` appended. Any utm_* the seller's URL already carries is replaced
    (it belongs to whoever linked them before); every other parameter is kept byte for byte,
    since sellers' own query strings are not always encoded the way urlencode would."""
    if not params:
        return url
    parts = urlsplit(url)
    kept = [p for p in parts.query.split("&") if p and not p.lower().startswith("utm_")]
    query = "&".join([*kept, urlencode(params)])
    return urlunsplit(parts._replace(query=query))


def tagged(url: str, cfg: UtmSettings, row: EventRow, placement: str | None = None) -> str:
    return tag_url(url, utm_params(cfg, row, placement)) if cfg.enabled else url
