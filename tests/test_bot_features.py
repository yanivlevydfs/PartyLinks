"""Phases 1b, 1c and 2 of the bot on a real database: the buttons under a card (⏰ remind, 👁 watch,
🔁 similar, ➕ calendar / who's coming / follow), /reminders, /digest, the jobs that send them, the
broadcast channel's picks, and the usage counts of the admin's bot tab."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from partylinks.api import create_app
from partylinks.bot.core import channel, digest, forecast, remind, usage
from partylinks.bot.core.conversation import Conversation
from partylinks.bot.core.reply import ALERT_YES, MAX_ACTION_BYTES, OPTIONS, Reply
from partylinks.bot.models import BotAlertRow, BotChatRow, BotReminderRow, BotUsageRow, BotWatchRow
from partylinks.bot.telegram import jobs, render
from partylinks.config import Settings
from partylinks.db import Database, EventRow
from partylinks.models import TicketTier
from partylinks.plans.models import PlanRow
from tests.test_storage_api import event, seed, settings_for

CHAT = "2002"
NOW = datetime.now(ZoneInfo("Asia/Jerusalem"))
SOON = NOW.replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
SATURDAY = (NOW + timedelta(days=(5 - NOW.weekday()) % 7)).replace(hour=23, minute=0, second=0, microsecond=0)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send_message(self, chat_id: Any, text: str, **kw: Any) -> None:
        self.sent.append(("text", {"chat_id": chat_id, "text": text, **kw}))

    async def send_photo(self, chat_id: Any, photo: str, **kw: Any) -> None:
        self.sent.append(("photo", {"chat_id": chat_id, "photo": photo, **kw}))

    async def send_document(self, chat_id: Any, document: Any, **kw: Any) -> None:
        self.sent.append(("document", {"chat_id": chat_id, "document": document, **kw}))


@pytest.fixture(autouse=True)
def _no_weather(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*args: Any, **kwargs: Any) -> list[list[Any]]:
        return []

    monkeypatch.setattr(forecast.weather, "forecast", none)


def _ticket(price: int) -> list[TicketTier]:
    return [TicketTier(name="Entry", price=Decimal(price))]


def _events() -> list[Any]:
    return [
        event("a", SOON, title="Techno Bunker", tickets=_ticket(120), genres=["techno"], venue_name="The Block",
              artists=["Amelie Lens"]),
        event("b", SOON + timedelta(days=1), title="Techno Garden", tickets=_ticket(60), genres=["techno"]),
        event("j", SOON, title="Jazz Club Night", tickets=_ticket(50), genres=["jazz"], address="Jerusalem, Israel"),
        event("w", SATURDAY, title="Saturday Rave", tickets=_ticket(70), genres=["techno"]),
    ]  # fmt: skip


def _talk(tmp_path: Path) -> tuple[Conversation, Database, Settings]:
    settings = settings_for(tmp_path)
    db = seed(settings, _events())
    return Conversation(lambda: settings, db, "telegram"), db, settings


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _card(talk: Conversation, title: str) -> Any:
    reply = _run(talk.text(CHAT, title, "en"))
    return next(c for c in reply.cards if c.title == title)


def _action(card: Any, prefix: str) -> str:
    return next(b.action for b in card.actions if b.action and b.action.startswith(prefix))


def _count(db: Database, model: Any) -> int:
    with db.session(write=False) as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def test_every_card_has_its_buttons_within_telegrams_limit(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path)
    card = _card(talk, "Techno Bunker")
    assert [b.action.split(":")[0] for b in card.actions] == ["rem", "wat", "sim", "opt"]
    assert all(len(b.action.encode()) <= MAX_ACTION_BYTES for b in card.actions)
    bot = FakeBot()
    _run(render.send_card(cast(Any, bot), CHAT, card))
    assert len(bot.sent[0][1]["reply_markup"].inline_keyboard) == 2  # links, then the actions


def test_remind_me_sets_both_offsets_and_a_second_tap_cancels(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    remind_me = _action(_card(talk, "Techno Bunker"), "rem:")
    assert "day" in _run(talk.press(CHAT, remind_me, "en")).text
    with db.session(write=False) as s:
        hours = sorted(round((r.event_start - r.remind_at).total_seconds() / 3600) for r in s.execute(
            select(BotReminderRow.remind_at, EventRow.start_at.label("event_start"))
            .join(EventRow, EventRow.id == BotReminderRow.event_id)
        ))  # fmt: skip
    assert hours == [2, 24]
    _run(talk.press(CHAT, remind_me, "en"))
    assert _count(db, BotReminderRow) == 0


def test_a_reminder_is_sent_once_when_its_time_comes(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path)
    _run(talk.press(CHAT, _action(_card(talk, "Techno Bunker"), "rem:"), "en"))
    with db.session() as s:  # the 24 hours one is due
        s.execute(update(BotReminderRow).where(BotReminderRow.remind_at < SOON - timedelta(hours=3))
                  .values(remind_at=datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)))  # fmt: skip
    bot = FakeBot()
    assert _run(jobs.send_reminders(settings, db, cast(Any, bot))) == 1
    assert "Techno Bunker" in bot.sent[0][1]["text"] and "3 days" in bot.sent[0][1]["text"]  # what is left
    assert _run(jobs.send_reminders(settings, db, cast(Any, FakeBot()))) == 0  # never twice


def test_a_watch_tells_of_a_price_drop_once_and_of_a_sell_out(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path)
    watch = _action(_card(talk, "Techno Bunker"), "wat:")
    assert "Watching" in _run(talk.press(CHAT, watch, "en")).text
    assert remind.due_watches(db, settings.bot.watch_min_drop_percent) == []
    with db.session() as s:
        s.execute(update(EventRow).where(EventRow.title == "Techno Bunker").values(group_min_price=Decimal(90)))
    bot = FakeBot()
    assert _run(jobs.send_watches(settings, db, cast(Any, bot))) == 1
    assert "120" in bot.sent[0][1]["text"] and "90" in bot.sent[0][1]["text"]
    assert _run(jobs.send_watches(settings, db, cast(Any, FakeBot()))) == 0
    with db.session() as s:
        s.execute(update(EventRow).where(EventRow.title == "Techno Bunker").values(is_sold_out=True))
    assert [n.kind for n in remind.due_watches(db, settings.bot.watch_min_drop_percent)] == ["sold_out"]
    _run(talk.press(CHAT, watch, "en"))  # pressed again: stops
    assert _count(db, BotWatchRow) == 0


def test_similar_is_the_same_kind_never_the_event_itself(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path)
    reply = _run(talk.press(CHAT, _action(_card(talk, "Techno Bunker"), "sim:"), "en"))
    titles = [c.title for c in reply.cards]
    assert "Techno Bunker" not in titles and "Techno Garden" in titles and "Jazz Club Night" not in titles


def test_the_more_menu_calendar_plan_and_follow(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    menu = _run(talk.press(CHAT, _action(_card(talk, "Techno Bunker"), OPTIONS), "en"))
    actions = {b.label: b.action for row in menu.buttons for b in row if b.action}
    urls = [b.url for row in menu.buttons for b in row if b.url]
    assert any(u.startswith("https://calendar.google.com/") for u in urls)
    ics = _run(talk.press(CHAT, next(a for a in actions.values() if a.startswith("ics:")), "en"))
    assert ics.document and b"BEGIN:VCALENDAR" in ics.document[1] and b"Techno Bunker" in ics.document[1]
    plan = _run(talk.press(CHAT, next(a for a in actions.values() if a.startswith("who:")), "en"))
    with db.session(write=False) as s:
        plan_id = s.scalar(select(PlanRow.id))
    assert plan_id and f"/plan/{plan_id}" in plan.text
    assert any("t.me/share" in (b.url or "") for row in plan.buttons for b in row)
    follow = next(a for label, a in actions.items() if "The Block" in label)
    asked = _run(talk.press(CHAT, follow, "en"))
    assert "The Block" in asked.text and _count(db, BotAlertRow) == 0  # never without the confirm
    _run(talk.press(CHAT, ALERT_YES, "en"))
    with db.session(write=False) as s:
        assert list(s.scalars(select(BotAlertRow.query))) == ["The Block"]
    assert any("Amelie Lens" in label for label in actions)


def test_my_reminders_lists_and_deletes(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    card = _card(talk, "Techno Bunker")
    _run(talk.press(CHAT, _action(card, "rem:"), "en"))
    _run(talk.press(CHAT, _action(card, "wat:"), "en"))
    listing = _run(talk.reminders(CHAT, "en"))
    assert len(listing.buttons) == 2
    _run(talk.press(CHAT, listing.buttons[0][0].action, "en"))
    _run(talk.press(CHAT, _run(talk.reminders(CHAT, "en")).buttons[0][0].action, "en"))
    assert _count(db, BotReminderRow) == _count(db, BotWatchRow) == 0
    assert not _run(talk.reminders(CHAT, "en")).buttons


def test_a_stale_button_says_it_is_gone(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path)
    assert "no longer" in _run(talk.press(CHAT, "rem:999999", "en")).text
    assert "no longer" in _run(talk.press(CHAT, "rem:abc", "en")).text


def test_the_digest_is_opt_in_weekly_and_once(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path)
    assert digest.due(settings, db) == []  # nobody asked
    ask = _run(talk.digest(CHAT, "en"))
    _run(talk.press(CHAT, ask.buttons[0][0].action, "en"))  # everything
    sent = digest.due(settings, db)
    assert len(sent) == 1 and sent[0].event_ids
    with db.session(write=False) as s:
        titles = set(s.scalars(select(EventRow.title).where(EventRow.id.in_(sent[0].event_ids))))
    assert "Saturday Rave" in titles
    assert digest.due(settings, db) == []  # a week before the next
    _run(talk.press(CHAT, "dig:off", "en"))
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.digest_on)) is False


def test_the_channel_never_posts_an_event_twice(tmp_path: Path) -> None:
    _, db, settings = _talk(tmp_path)
    settings = settings.model_copy(update={"bot": settings.bot.model_copy(update={"digest_weekday": NOW.weekday()})})
    first = channel.due(settings, db, "@spotx")
    assert "test:w" in first  # the weekend's picks
    assert set(channel.due(settings, db, "@spotx")).isdisjoint(first)


def test_usage_is_counted_for_the_admin_and_stop_unlinks_it(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path)
    _run(talk.text(CHAT, "techno", "en"))
    _run(talk.text(CHAT, "zzqqxx", "en"))
    _run(talk.inline("jazz", "en"))
    with db.session(write=False) as s:
        stats = usage.stats(s, settings.timezone, 7)
    assert stats["people"] == 1 and stats["per_kind"]["search"] == 2 and stats["per_kind"]["inline"] == 1
    assert [r["q"] for r in stats["top_misses"]] == ["zzqqxx"] and stats["now"]["chats"] == 1
    assert stats["per_day"][-1]["searches"] == 2
    with TestClient(create_app(settings)) as client:
        tab = client.get("/admin/api/bot?days=7").json()
    assert tab["per_kind"]["search"] == 2 and tab["setup"]["token"] is False
    _run(talk.stop(CHAT, "en"))
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotUsageRow).where(BotUsageRow.chat.is_not(None))) == 0


def test_a_reply_with_a_file_sends_it() -> None:
    bot = FakeBot()
    _run(render.send(cast(Any, bot), CHAT, Reply("📅", document=("event.ics", b"BEGIN:VCALENDAR")), "en"))
    assert [kind for kind, _ in bot.sent] == ["text", "document"]
