"""The bot's conversation on a real database: search, follow-ups, more, near me, cheapest, widening,
alerts (only with a confirm), /stop. No chat app: the core's Reply objects (partylinks.bot.core)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from partylinks.bot.core import forecast
from partylinks.bot.core.conversation import Conversation
from partylinks.bot.core.reply import ALERT, ALERT_NO, ALERT_YES, MORE, Reply
from partylinks.bot.models import BotAlertRow, BotChatRow
from partylinks.config import Settings
from partylinks.db import Database
from partylinks.models import TicketTier
from tests.test_storage_api import event, seed, settings_for

CHAT = "1001"
SOON = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
SLOTS = [[(SOON - timedelta(hours=3)).isoformat(), "partlycloudy_night", 21.4, 0.0, 6]]


@pytest.fixture(autouse=True)
def _weather(monkeypatch: pytest.MonkeyPatch) -> None:
    """MET's forecast without the network: one slot over every test event's start."""

    async def fake(*args: Any, **kwargs: Any) -> list[list[Any]]:
        return SLOTS

    monkeypatch.setattr(forecast.weather, "forecast", fake)


def _settings(tmp_path: Path) -> Settings:
    s = settings_for(tmp_path)
    return s.model_copy(update={"bot": s.bot.model_copy(update={"results": 2})})


def _talk(tmp_path: Path, events: list[Any]) -> tuple[Conversation, Database, Settings]:
    settings = _settings(tmp_path)
    db = seed(settings, events)
    return Conversation(lambda: settings, db, "test"), db, settings


def _run(coro: Any) -> Reply:
    return asyncio.run(coro)


def _ticket(price: int) -> list[TicketTier]:
    return [TicketTier(name="Entry", price=Decimal(price))]


def _events() -> list[Any]:
    return [
        event("a", SOON, title="Techno Bunker", tickets=_ticket(120), latitude=32.0853, longitude=34.7818),
        event("b", SOON + timedelta(hours=1), title="Techno Garden", tickets=_ticket(60), latitude=32.07, longitude=34.79),
        event("c", SOON + timedelta(hours=2), title="Techno Roof", tickets=_ticket(90), latitude=32.80, longitude=34.99,
              address="Haifa, Israel"),
        event("j", SOON, title="Jazz Club Night", tickets=_ticket(50), latitude=31.77, longitude=35.21, address="Jerusalem, Israel"),
    ]  # fmt: skip


def test_a_search_answers_with_cards_links_and_weather(tmp_path: Path) -> None:
    talk, _, settings = _talk(tmp_path, _events())
    reply = _run(talk.text(CHAT, "מה יש טכנו?", "he"))
    assert reply.text and len(reply.cards) == 2  # bot.results
    card = reply.cards[0]
    assert card.title.startswith("Techno")
    assert any(line.startswith("🗓") for line in card.lines)
    assert any("21°" in line for line in card.lines), card.lines  # weather on every placed event
    assert any(line.startswith("💸") for line in card.lines)
    tickets, details = card.buttons
    base = settings.site.base_url.rstrip("/")
    assert tickets.url == f"{base}/go/{card.event_id.replace(':', '%3A')}?at=tg"
    assert details.url.startswith(f"{base}/event/") and "utm_source=telegram" in details.url
    assert [b.action for b in reply.buttons[0]] == [MORE, ALERT]  # 3 found, 2 shown


def test_more_continues_and_ends(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    first = _run(talk.text(CHAT, "techno", "en"))
    second = _run(talk.press(CHAT, MORE, "en"))
    assert len(second.cards) == 1
    assert {c.event_id for c in first.cards}.isdisjoint(c.event_id for c in second.cards)
    assert MORE not in [b.action for row in second.buttons for b in row]
    assert not _run(talk.press(CHAT, MORE, "en")).cards  # nothing left: says so


def test_a_follow_up_changes_only_the_place(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "טכנו בתל אביב", "he"))
    reply = _run(talk.text(CHAT, "ובחיפה?", "he"))
    assert [c.title for c in reply.cards] == ["Techno Roof"]


def test_cheapest_first(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    reply = _run(talk.text(CHAT, "טכנו הכי זול", "he"))
    assert [c.title for c in reply.cards] == ["Techno Garden", "Techno Roof"]


def test_near_me_asks_for_the_location_then_sorts_by_distance(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    ask = _run(talk.text(CHAT, "טכנו קרוב אליי", "he"))
    assert ask.ask_location and not ask.cards
    near = _run(talk.location(CHAT, 32.0853, 34.7818, "he"))
    assert [c.title for c in near.cards] == ["Techno Bunker", "Techno Garden"]  # Haifa is too far
    assert any("ק״מ ממך" in line for line in near.cards[1].lines)
    assert ALERT not in [b.action for row in near.buttons for b in row]  # a place is no alert


def test_nothing_found_widens_the_date_and_says_so(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    reply = _run(talk.text(CHAT, "jazz tonight", "en"))  # the jazz night is in 3 days
    assert [c.title for c in reply.cards] == ["Jazz Club Night"]
    assert "date" in reply.text or "dates" in reply.text


def test_nothing_at_all_says_so_kindly(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    reply = _run(talk.text(CHAT, "zzqqxx", "en"))
    assert not reply.cards and "zzqqxx" in reply.text and reply.buttons


def test_an_alert_needs_a_confirm(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "techno", "en"))

    def alerts() -> list[str]:
        with db.session(write=False) as s:
            return list(s.scalars(select(BotAlertRow.query)))

    ask = _run(talk.press(CHAT, ALERT, "en"))
    assert [b.action for b in ask.buttons[0]] == [ALERT_YES, ALERT_NO] and alerts() == []
    _run(talk.press(CHAT, ALERT_NO, "en"))
    assert alerts() == [] and _run(talk.press(CHAT, ALERT_YES, "en")).text  # nothing pending any more
    assert alerts() == []
    _run(talk.press(CHAT, ALERT, "en"))
    _run(talk.press(CHAT, ALERT_YES, "en"))
    assert alerts() == ["techno"]
    listing = _run(talk.alerts(CHAT, "en"))
    delete = listing.buttons[0][0].action
    assert delete and delete.startswith("del:")
    _run(talk.press(CHAT, delete, "en"))
    assert alerts() == []


def test_alerts_per_chat_are_capped(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path, _events())
    for word in [f"word{i}" for i in range(settings.bot.max_alerts + 1)]:
        _run(talk.text(CHAT, f"techno {word}", "en"))
        _run(talk.press(CHAT, ALERT, "en"))
        _run(talk.press(CHAT, ALERT_YES, "en"))
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotAlertRow)) == settings.bot.max_alerts


def test_stop_deletes_everything(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "techno", "en"))
    _run(talk.press(CHAT, ALERT, "en"))
    _run(talk.press(CHAT, ALERT_YES, "en"))
    _run(talk.stop(CHAT, "en"))
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotChatRow)) == 0
        assert s.scalar(select(func.count()).select_from(BotAlertRow)) == 0


def test_the_language_follows_the_chat_until_chosen(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "techno", "he"))
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.lang)) == "en"
    _run(talk.set_lang(CHAT, "he"))
    _run(talk.text(CHAT, "techno", "en"))
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.lang)) == "he"  # chosen: kept


def test_past_events_never_show(tmp_path: Path) -> None:
    past = event("old", datetime.now(UTC) - timedelta(days=2), title="Techno Yesterday")
    talk, _, _ = _talk(tmp_path, [past, *_events()])
    reply = _run(talk.text(CHAT, "techno", "en"))
    assert "Techno Yesterday" not in [c.title for c in reply.cards]


def test_inline_mode_gives_cards_without_memory(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path, _events())
    found = asyncio.run(talk.inline("jazz", "en"))
    assert [c.title for c in found] == ["Jazz Club Night"]
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotChatRow)) == 0


def test_a_follow_up_of_near_me_stays_near(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "טכנו קרוב אליי", "he"))
    _run(talk.location(CHAT, 32.0853, 34.7818, "he"))
    cheap = _run(talk.text(CHAT, "הכי זול", "he"))
    assert [c.title for c in cheap.cards] == ["Techno Garden", "Techno Bunker"]  # still near: no Haifa
    assert any("ק״מ ממך" in line for line in cheap.cards[0].lines)
    haifa = _run(talk.text(CHAT, "ובחיפה", "he"))  # names a place: that place, not near any more
    assert [c.title for c in haifa.cards] == ["Techno Roof"]


def test_two_taps_on_yes_save_one_alert(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path, _events())
    _run(talk.text(CHAT, "techno", "en"))
    _run(talk.press(CHAT, ALERT, "en"))

    async def both() -> list[Reply]:
        return list(await asyncio.gather(talk.press(CHAT, ALERT_YES, "en"), talk.press(CHAT, ALERT_YES, "en")))

    asyncio.run(both())
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotAlertRow)) == 1
