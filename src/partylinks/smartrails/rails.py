"""Home rows read from what only we know: open now, free if you come now, the price dropped, revealed today,
last tickets, and tonight's weather (rainy day / a perfect evening outside). Settings: ``smart_rails``.

``rail_filters`` gives each row's WHERE clauses (the API's own event filters plus the row's condition); the
API loads and orders them like every home row. ``was_prices`` gives a card its price before a recent drop
("₪120 → ₪90"), on every list, not only the row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from partylinks.config import Settings
from partylinks.db import DealChangeRow, EventRow

# The API's event filters (api.build_event_filters with its settings bound): keyword arguments -> clauses.
Filters = Callable[..., list[ColumnElement[bool]]]


def _changed_groups(cfg: Settings, now: datetime) -> Any:
    """Groups (or lone listings) with a price drop in the last ``price_down_days``."""
    listing = aliased(EventRow)
    since = now - timedelta(days=cfg.smart_rails.price_down_days)
    return (
        select(func.coalesce(listing.group_id, listing.id))
        .join(DealChangeRow, DealChangeRow.event_id == listing.id)
        .where(DealChangeRow.kind == "price_down", DealChangeRow.at >= since)
    )


def _weather_rails(cfg: Settings, now: datetime, filters: Filters, slots: list[list[Any]]) -> dict[str, list[ColumnElement[bool]]]:
    """"rainy" or "outside" tonight, by the forecast slot covering ``weather_hour`` (none without a forecast)."""
    s = cfg.smart_rails
    hour = now.replace(hour=s.weather_hour, minute=0, second=0, microsecond=0)
    slot = next(
        (x for x in slots if datetime.fromisoformat(x[0]) <= hour < datetime.fromisoformat(x[0]) + timedelta(hours=x[4])),
        None,
    )
    if slot is None:
        return {}
    _, _, temp, rain, _ = slot
    tonight = {"on_date": now.date()}
    if rain is not None and rain >= s.rain_mm and s.indoor_genres:
        return {"rainy": filters(genre=s.indoor_genres, **tonight)}
    if (rain or 0) < s.rain_mm and temp is not None and s.outdoor_min_c <= temp <= s.outdoor_max_c and s.outdoor_genres:
        return {"outside": filters(genre=s.outdoor_genres, **tonight)}
    return {}


def rail_filters(
    cfg: Settings, now: datetime, filters: Filters, slots: list[list[Any]]
) -> dict[str, list[ColumnElement[bool]]]:
    """{row key: WHERE clauses}, in screen order. ``now``: aware, in the site's time zone; ``slots``: tonight's
    forecast at ``weather_city`` (weather.cached_slots), [] when there is none."""
    s = cfg.smart_rails
    if not s.enabled:
        return {}
    month = {"date_to": now + timedelta(days=s.window_days)}
    free_until = now + timedelta(hours=s.free_now_hours)
    sold_out = aliased(EventRow)
    sold_somewhere = select(sold_out.group_id).where(sold_out.group_id.is_not(None), sold_out.is_sold_out.is_(True))
    return {
        "now": filters(open_now=True),
        **_weather_rails(cfg, now, filters, slots),
        "free_now": [
            *filters(free_entry=True, date_to=free_until),
            EventRow.group_free_entry_until.is_not(None),
            EventRow.group_free_entry_until <= free_until,
        ],
        "price_down": [*filters(**month), func.coalesce(EventRow.group_id, EventRow.id).in_(_changed_groups(cfg, now))],
        "new_today": [*filters(**month), EventRow.first_seen_at >= now - timedelta(hours=s.new_hours)],
        # Selling out: its free tickets are gone, or another site sold out while this one still sells.
        "last_tickets": [
            *filters(sold_out=False, **month),
            or_(EventRow.free_sold_out.is_(True), EventRow.group_id.in_(sold_somewhere)),
        ],
    }


def _price(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def was_prices(session: Session, cfg: Settings, rows: Sequence[Any], now: datetime) -> dict[str, float]:
    """{id: the highest price before a drop in the last ``price_down_days``} for cards now cheaper than that."""
    keys = {r.id: r.group_id or r.id for r in rows}
    if not keys:
        return {}
    since = now - timedelta(days=cfg.smart_rails.price_down_days)
    listing = aliased(EventRow)
    group = func.coalesce(listing.group_id, listing.id)
    found = session.execute(
        select(group, DealChangeRow.old)
        .join(DealChangeRow, DealChangeRow.event_id == listing.id)
        .where(DealChangeRow.kind == "price_down", DealChangeRow.at >= since, group.in_(set(keys.values())))
    ).all()
    before: dict[str, Decimal] = {}
    for key, old in found:
        if (price := _price(old)) is not None and price > before.get(key, Decimal(0)):
            before[key] = price
    out: dict[str, float] = {}
    for r in rows:
        now_price = r.group_min_price if r.group_min_price is not None else r.min_price
        was = before.get(keys[r.id])
        if was is not None and now_price is not None and was > Decimal(str(now_price)):
            out[r.id] = float(was)
    return out


__all__ = ["Filters", "rail_filters", "was_prices"]
