"""Home rows only we can make (partylinks.smartrails): free if you go now, price dropped, revealed today,
last tickets, open now and tonight's weather; and a card's price before a drop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import DealChangeRow
from partylinks.models import TicketTier
from partylinks.smartrails.rails import rail_filters
from tests.test_storage_api import event, seed, settings_for


def _free_until(until: datetime) -> list[TicketTier]:
    return [TicketTier(name="Free", price=Decimal(0), sale_end=until), TicketTier(name="Entry", price=Decimal(80))]


def test_rows_and_the_price_before_a_drop(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    soon = now + timedelta(hours=2)
    db = seed(settings, [
        event("free", now + timedelta(hours=1), title="Free before midnight", tickets=_free_until(soon)),
        event("cheaper", now + timedelta(days=3), title="Cheaper now"),
        event("going", now - timedelta(hours=1), title="On right now", end_at=now + timedelta(hours=3)),
        event("later", now + timedelta(days=5), title="Free entry later", tickets=_free_until(now + timedelta(days=5, hours=1))),
    ])  # fmt: skip
    with db.session() as session:  # the scan saw "Cheaper now" drop from 120 to its 80
        session.add(DealChangeRow(event_id="test:cheaper", source="test", kind="price_down", old="120", new="80", at=now))
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        rails = {k: {e["title"] for e in v} for k, v in client.get("/events/rails").json()["rails"].items()}
        keys = list(rails)
        assert keys.index("now") == keys.index("tonight") + 1  # right after the `after` row
        assert rails["free_now"] == {"Free before midnight"}  # not one whose free entry ends in five days
        assert rails["price_down"] == {"Cheaper now"}
        assert rails["now"] == {"On right now"}
        assert {"Free before midnight", "Cheaper now", "On right now"} <= rails["new_today"]
        items = {e["title"]: e for e in client.get("/events").json()["items"]}
        assert items["Cheaper now"]["was_price"] == 120 and items["On right now"]["was_price"] is None


def test_weather_row_follows_tonights_forecast() -> None:
    cfg = Settings()
    tz = ZoneInfo(cfg.timezone)
    now = datetime(2026, 11, 6, 15, 0, tzinfo=tz)
    slot = now.replace(hour=cfg.smart_rails.weather_hour).astimezone(UTC).isoformat()

    def rows(temp: float, rain: float) -> set[str]:
        return set(rail_filters(cfg, now, lambda **kw: [], [[slot, "x", temp, rain, 1]]))

    assert "rainy" in rows(15, 3.0) and "outside" not in rows(15, 3.0)
    assert "outside" in rows(24, 0.0)
    assert not {"rainy", "outside"} & rows(10, 0.0)  # dry but cold: neither
    assert not {"rainy", "outside"} & set(rail_filters(cfg, now, lambda **kw: [], []))  # no forecast cached
