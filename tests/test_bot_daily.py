"""☀️ The daily tip: opt-in with an area (and the last search's taste), tonight's picks sent once a day,
nothing when there is nothing tonight, off with a second /daily, gone with /stop."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from partylinks.bot.core import daily, menu
from partylinks.bot.core.conversation import Conversation
from partylinks.bot.models import BotChatRow, BotUsageRow
from partylinks.bot.telegram import jobs
from tests.test_bot_features import CHAT, FakeBot, _run
from tests.test_storage_api import event, seed, settings_for

TONIGHT = datetime.now(ZoneInfo("Asia/Jerusalem")).replace(hour=23, minute=30, second=0, microsecond=0)


def _talk(tmp_path: Path, events: list[Any]) -> tuple[Conversation, Any, Any]:
    settings = settings_for(tmp_path)
    db = seed(settings, events)
    return Conversation(lambda: settings, db, "telegram"), db, settings


def _kinds(db: Any) -> dict[str, int]:
    with db.session(write=False) as s:
        return dict(s.execute(select(BotUsageRow.kind, func.count()).group_by(BotUsageRow.kind)).all())  # type: ignore[arg-type]


def test_opt_in_sends_tonights_picks_once_and_a_second_daily_stops(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path, [
        event("t", TONIGHT, title="Techno Tonight", genres=["techno"]),
        event("j", TONIGHT, title="Jazz Tonight", genres=["jazz"]),
        event("m", TONIGHT + timedelta(days=1), title="Techno Tomorrow", genres=["techno"]),
    ])  # fmt: skip
    assert "dy:" in [b.action for row in menu.home(settings, "en").buttons for b in row]
    _run(talk.text(CHAT, "techno tomorrow", "en"))
    ask = _run(talk.press(CHAT, daily.PREFIX, "en"))
    assert '"techno"' in ask.text and daily.PREFIX + daily.EVERYWHERE in [b.action for row in ask.buttons for b in row]
    assert daily.due(settings, db) == []  # nobody subscribed yet
    on = _run(talk.press(CHAT, daily.PREFIX + daily.EVERYWHERE, "en"))
    assert "techno" in on.text
    items = daily.due(settings, db)
    assert [i.event_ids for i in items] == [["test:t"]]  # tonight's, the taste's only
    assert daily.due(settings, db) == []  # once a day
    bot = FakeBot()
    assert _run(jobs.send_picks(settings, db, cast(Any, bot), items, daily.intro, "daily_sent")) == 1
    assert "daily tip" in bot.sent[0][1]["text"]
    assert "stopped" in _run(talk.press(CHAT, daily.PREFIX, "en")).text
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.daily_query)) is None
    assert {k: _kinds(db)[k] for k in ("daily_on", "daily_off", "daily_sent")} == {
        "daily_on": 1,
        "daily_off": 1,
        "daily_sent": 1,
    }


def test_nothing_tonight_sends_nothing_and_stop_removes_it(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path, [event("m", TONIGHT + timedelta(days=2), title="Later")])
    _run(talk.press(CHAT, daily.PREFIX + "gush_dan", "he"))
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.daily_query)) == menu.AREAS["gush_dan"][0]
    assert daily.due(settings, db) == []
    _run(talk.stop(CHAT, "he"))
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotChatRow)) == 0
