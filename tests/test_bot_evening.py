"""🌙 Plan my evening in the bot: the site's evening around an event, its steps in order, area buttons."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from partylinks.bot.core import menu
from partylinks.bot.core.conversation import Conversation
from partylinks.bot.core.reply import MAX_ACTION_BYTES
from partylinks.bot.models import BotUsageRow
from tests.test_bot_features import CHAT, _run
from tests.test_evening import CLUB, _place
from tests.test_storage_api import event, seed, settings_for


def _talk(tmp_path: Path) -> tuple:
    settings = settings_for(tmp_path)
    tz = ZoneInfo(settings.timezone)
    day = datetime.now(tz).date() + timedelta(days=1)
    db = seed(settings, [
        event("party", datetime.combine(day, time(23), tz), title="Techno night", latitude=CLUB[0], longitude=CLUB[1]),
        _place("bar", datetime.combine(day, time(19), tz), 2, "happy hour", (32.0820, 34.7810)),
        _place("chef", datetime.combine(day, time(18), tz), 4, "fine dining", (32.0805, 34.7805)),
        # Its happy hour at noon, but open till 06:00 (extra.hours): the stop after a party ending at 05:00.
        _place("night", datetime.combine(day, time(12), tz), 2, "happy hour", (32.0790, 34.7790), open_hours="12:00-06:00"),
    ])  # fmt: skip
    return Conversation(lambda: settings, db, "telegram"), db, settings


def _actions(reply: object) -> list[str]:
    return [b.action for row in reply.buttons for b in row if b.action]  # type: ignore[attr-defined]


def test_an_evening_in_words_is_the_sites_plan_in_order(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    reply = _run(talk.text(CHAT, "תכנן לי ערב בתל אביב מחר", "he"))
    assert [c.title for c in reply.cards] == [
        "🍸 לפני: האפי האוור bar", "🍽️ לפני: ארוחה chef", "🎉 האירוע: Techno night", "🍻 אחרי: בר או אוכל בלילה night",
    ]  # fmt: skip
    assert reply.cards[0].lines[-1].startswith("🚶") and "מ׳" in reply.cards[0].lines[-1]
    another = next(a for a in _actions(reply) if a.startswith("ev:"))
    assert another.startswith("ev:1:") and len(another.encode()) <= MAX_ACTION_BYTES
    nothing = _run(talk.press(CHAT, another, "he"))  # no other event that day
    assert not nothing.cards and "🤷" in nothing.text
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotUsageRow).where(BotUsageRow.kind == "evening")) == 2


def test_no_place_asks_the_area_or_takes_the_last_search(tmp_path: Path) -> None:
    talk, _, settings = _talk(tmp_path)
    assert "ev:0:" in _actions(menu.home(settings, "en"))
    ask = _run(talk.text(CHAT, "Plan an evening tomorrow", "en"))
    assert not ask.cards and "ev:0:tomorrow center" in _actions(ask)
    _run(talk.text(CHAT, "techno in tel aviv", "en"))
    reply = _run(talk.text(CHAT, "plan my evening tomorrow", "en"))
    assert "tel aviv" in reply.text and "🎉 The event: Techno night" in [c.title for c in reply.cards]
