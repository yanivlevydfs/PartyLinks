"""🔥 the week's top and 🎊 the coming holiday in the bot's menu: the site's picks as cards, "more" by offset."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from partylinks.bot.core import menu, understand
from partylinks.bot.core.conversation import Conversation
from partylinks.bot.core.reply import MAX_ACTION_BYTES
from partylinks.bot.models import BotUsageRow
from partylinks.holiday_pages.core import upcoming
from tests.test_bot_features import CHAT, _events, _run, _ticket
from tests.test_storage_api import event, seed, settings_for


def _talk(tmp_path: Path, rail_days: int) -> tuple:
    settings = settings_for(tmp_path)
    settings.bot.results = 2
    settings.holiday_pages.rail_days = rail_days
    holiday = upcoming(settings, understand.today_in(settings))[0]
    night = datetime.combine(holiday.end, time(21), ZoneInfo(settings.timezone))
    db = seed(settings, [*_events(), event("h", night, title="Holiday Party", tickets=_ticket(80))])
    return Conversation(lambda: settings, db, "telegram"), db, settings, holiday


def _actions(reply: object) -> list[str]:
    return [b.action for row in reply.buttons for b in row if b.action]  # type: ignore[attr-defined]


def test_top_of_the_week_pages_with_more_and_counts_usage(tmp_path: Path) -> None:
    talk, db, settings, _ = _talk(tmp_path, rail_days=0)
    home = menu.home(settings, "en")
    assert "hl:t:0" in _actions(home) and not any(a.startswith("hl:h:") for a in _actions(home))
    first = _run(talk.press(CHAT, "hl:t:0", "en"))
    assert first.text.startswith("🔥") and len(first.cards) == 2 and "hl:t:2" in _actions(first)
    rest = _run(talk.press(CHAT, "hl:t:2", "en"))
    assert rest.cards and not {c.event_id for c in rest.cards} & {c.event_id for c in first.cards}
    assert _run(talk.text(CHAT, "What's hot this week?", "en")).text.startswith("🔥")
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotUsageRow).where(BotUsageRow.kind == "top")) == 3


def test_a_coming_holiday_gets_a_button_and_its_events(tmp_path: Path) -> None:
    talk, _, settings, holiday = _talk(tmp_path, rail_days=90)
    button = f"hl:h:{holiday.key}:0"
    home = menu.home(settings, "he")
    assert button in _actions(home) and all(len(a.encode()) <= MAX_ACTION_BYTES for a in _actions(home))
    assert any(b.label.endswith(f"אירועי {holiday.he}") for row in home.buttons for b in row)
    reply = _run(talk.press(CHAT, button, "en"))
    assert holiday.en in reply.text and [c.title for c in reply.cards] == ["Holiday Party"]


def test_a_stale_holiday_button_says_nothing_to_show(tmp_path: Path) -> None:
    talk, *_ = _talk(tmp_path, rail_days=0)
    assert "Nothing to show" in _run(talk.press(CHAT, "hl:h:purim:0", "en")).text
