"""👥 Friends' groups: /find in a group -> compact cards (no personal buttons) and one poll with trimmed
options; plain group chatter ignored; removal deletes the group's row; private chats unchanged."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from telegram import Bot, Update

from partylinks.bot.core import group as core_group
from partylinks.bot.models import BotGroupRow, BotUsageRow
from partylinks.bot.telegram import group
from partylinks.bot.telegram.handlers import Handlers
from tests.test_bot_features import CHAT, SOON, FakeBot, _no_weather  # noqa: F401  (the fixture)
from tests.test_storage_api import event, seed, settings_for

GROUP = -100123
LONG = "🎉" * 10 + " Techno " + "x" * 150


class GroupBot(FakeBot):
    username = "spotxilbot"

    async def send_chat_action(self, chat_id: Any, action: str) -> None:
        pass

    async def send_poll(self, chat_id: Any, question: str, options: list[str], **kw: Any) -> None:
        self.sent.append(("poll", {"chat_id": chat_id, "question": question, "options": options, **kw}))


def _handlers(tmp_path: Path) -> tuple[Handlers, Any]:
    settings = settings_for(tmp_path)
    events = [event(str(n), SOON + timedelta(hours=n), title=f"Techno {n}", genres=["techno"]) for n in range(4)]
    db = seed(settings, [*events, event("long", SOON, title=LONG, genres=["techno"])])
    return Handlers(lambda: settings, db), db


def _update(payload: dict[str, Any]) -> Update:
    return Update.de_json({"update_id": 1, **payload}, Bot("1:x"))


def _message(text: str, chat_type: str = "supergroup", entities: list[dict[str, Any]] | None = None) -> Update:
    chat = {"id": GROUP if chat_type != "private" else int(CHAT), "type": chat_type, "title": "Friends"}
    return _update({"message": {
        "message_id": 1, "date": int(datetime.now(UTC).timestamp()), "chat": chat, "text": text,
        "from": {"id": 7, "is_bot": False, "first_name": "A"}, "entities": entities or [],
    }})  # fmt: skip


def _member(old: str, new: str) -> Update:
    user = {"id": 1, "is_bot": True, "first_name": "SPOTX"}
    return _update({"my_chat_member": {
        "chat": {"id": GROUP, "type": "supergroup", "title": "חברים"}, "from": {"id": 7, "is_bot": False, "first_name": "A"},
        "date": 0, "old_chat_member": {"status": old, "user": user}, "new_chat_member": {"status": new, "user": user},
    }})  # fmt: skip


def _groups(db: Any) -> int:
    with db.session(write=False) as s:
        return s.scalar(select(func.count()).select_from(BotGroupRow)) or 0


def test_find_in_a_group_sends_compact_cards_then_one_poll(tmp_path: Path) -> None:
    h, db = _handlers(tmp_path)
    bot = GroupBot()
    asyncio.run(group._find(h, _message("/find techno"), SimpleNamespace(bot=bot), "techno"))  # type: ignore[arg-type]
    kinds = [k for k, _ in bot.sent]
    assert len(kinds) == 5 and kinds[-1] == "poll"  # the intro, bot.group_results (3) cards, the poll
    for _, card in bot.sent[1:4]:
        assert all(b.url for row in card["reply_markup"].inline_keyboard for b in row)  # tickets + details only
    poll = bot.sent[-1][1]
    assert poll["question"] == "Where are we going?" and poll["is_anonymous"] is False
    assert poll["options"][-1] == "🤷 None" and len(poll["options"]) == 4
    assert all(len(o.encode("utf-16-le")) // 2 <= core_group.OPTION_MAX for o in poll["options"])
    assert any(o.endswith("…") for o in poll["options"])  # the long title, trimmed
    with db.session(write=False) as s:
        kinds_used = set(s.scalars(select(BotUsageRow.kind)))
    assert {"group_find", "group_poll"} <= kinds_used and _groups(db) == 1


def test_plain_group_chatter_is_ignored_and_a_mention_is_not(tmp_path: Path) -> None:
    h, _ = _handlers(tmp_path)
    app = SimpleNamespace(handlers=[], add_handler=lambda x: app.handlers.append(x), add_error_handler=lambda _: None)
    h.install(app)  # type: ignore[arg-type]
    plain = _message("who's coming tonight?")
    assert not any(x.check_update(plain) for x in app.handlers)
    mention = _message("@spotxilbot techno tonight", entities=[{"type": "mention", "offset": 0, "length": 11}])
    assert any(x.check_update(mention) for x in app.handlers)
    private = _message("techno tonight", "private")
    assert sum(bool(x.check_update(private)) for x in app.handlers) == 1  # on_text, as before


def test_added_posts_one_intro_and_removed_deletes_the_row(tmp_path: Path) -> None:
    h, db = _handlers(tmp_path)
    bot = GroupBot()
    context: Any = SimpleNamespace(bot=bot)
    asyncio.run(group._member(h, _member("left", "member"), context))
    assert len(bot.sent) == 1 and "/find" in bot.sent[0][1]["text"] and "@spotxilbot" in bot.sent[0][1]["text"]
    assert "מזהה הקבוצה" in bot.sent[0][1]["text"]  # the group's title is Hebrew: so is the intro
    asyncio.run(group._member(h, _member("member", "member"), context))
    assert len(bot.sent) == 1  # already in (e.g. made admin): no second intro
    asyncio.run(group._member(h, _member("member", "left"), context))
    assert _groups(db) == 0
    with db.session(write=False) as s:
        assert s.scalar(select(func.count()).select_from(BotUsageRow).where(BotUsageRow.chat.is_not(None))) == 0


def test_a_private_chat_is_unchanged(tmp_path: Path) -> None:
    h, db = _handlers(tmp_path)
    reply = asyncio.run(h.talk.text(CHAT, "techno", "en"))
    assert len(reply.cards) == 5 and all(c.actions for c in reply.cards)  # bot.results, with ⏰ 👁 🔁 ➕
    assert _groups(db) == 0
