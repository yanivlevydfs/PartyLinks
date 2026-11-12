"""👥 Friends' groups: added to a Telegram group, the bot answers ``/find <words>`` (or "@bot <words>") with
up to ``bot.group_results`` cards (tickets and details only: ⏰ 👁 are personal) and the poll's options,
"where are we going?". No entry screen: nothing about members is kept, only the group's id and language
while the bot is in it (``bot_groups``), deleted when it is removed (with the usage rows' link to it)."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from partylinks.bot.core import understand, usage
from partylinks.bot.core.card import cards
from partylinks.bot.core.conversation import DEFAULT_LANG, Conversation
from partylinks.bot.core.find import Found, search_or_widen
from partylinks.bot.core.phrases import say
from partylinks.bot.core.reply import Reply
from partylinks.bot.models import BotGroupRow

OPTION_MAX = 100  # Telegram: a poll option's characters (UTF-16 units); at most 10 options, so group_results ≤ 9
COMMAND = ("find", "חיפוש אירועים לקבוצה, למשל: /find טכנו הערב")
TEXT: dict[str, dict[str, str]] = {
    "intro": {
        "he": (
            "👋 היי, אני SPOTX! מחפשים לאן לצאת?\n"
            "כתבו /find ומה מחפשים, למשל: /find טכנו הערב בתל אביב (או @{bot} טכנו הערב)\n"
            "אשלח כמה אירועים וסקר: לאן הולכים?\n"
            "אני לא שומר כלום על חברי הקבוצה, רק את מזהה הקבוצה כל עוד אני בה."
        ),
        "en": (
            "👋 Hi, I'm SPOTX! Looking for a night out?\n"
            "Write /find and what you want, e.g. /find techno tonight in Tel Aviv (or @{bot} techno tonight)\n"
            "I'll send a few events and a poll: where are we going?\n"
            "I keep nothing about the group's members, only the group's id while I'm in it."
        ),
    },
    "ask": {
        "he": "כתבו /find ומה מחפשים, למשל: /find טכנו הערב בתל אביב",
        "en": "Write /find and what you want, e.g. /find techno tonight in Tel Aviv",
    },
    "question": {"he": "לאן הולכים?", "en": "Where are we going?"},
    "nobody": {"he": "🤷 אף אחד", "en": "🤷 None"},
}


def _lang(session: Session, channel: str, chat_id: str, lang: str) -> str:
    """The group's language; its row is added the first time (a group the bot joined before this existed too)."""
    session.execute(insert(BotGroupRow).values(channel=channel, chat_id=chat_id, lang=lang).on_conflict_do_nothing())
    return (
        session.scalar(select(BotGroupRow.lang).where(BotGroupRow.channel == channel, BotGroupRow.chat_id == chat_id))
        or lang
    )


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def option(title: str) -> str:
    """A poll option: the title, cut to Telegram's limit with "…"."""
    if _units(title) <= OPTION_MAX:
        return title
    cut = title[: OPTION_MAX - 1]
    while _units(cut) > OPTION_MAX - 1:  # an emoji is two units
        cut = cut[:-1]
    return cut.rstrip() + "…"


async def join(talk: Conversation, chat_id: str, title: str, bot: str) -> Reply:
    """The one intro when the bot is added: in the group title's language, Hebrew by default."""

    def work(session: Session) -> str:
        usage.record(session, talk.channel, chat_id, "group_join")
        return _lang(session, talk.channel, chat_id, understand.lang_of(title, DEFAULT_LANG))

    return Reply(TEXT["intro"][await talk._in_db(work)].format(bot=bot))


async def leave(talk: Conversation, chat_id: str) -> None:
    def work(session: Session) -> None:
        session.execute(delete(BotGroupRow).where(BotGroupRow.channel == talk.channel, BotGroupRow.chat_id == chat_id))
        usage.forget(session, chat_id)

    await talk._in_db(work)


async def find(talk: Conversation, chat_id: str, words: str) -> tuple[Reply, tuple[str, list[str]] | None]:
    """The cards, and the poll (question, options) when there are any."""
    cfg = talk.settings()
    message = understand.read(words, cfg)

    def work(session: Session) -> tuple[str, Found | None]:
        lang = understand.lang_of(words, _lang(session, talk.channel, chat_id, understand.lang_of(words, DEFAULT_LANG)))
        if message.kind != "search":
            return lang, None
        found = search_or_widen(
            session, cfg, message.query, understand.today_in(cfg), limit=cfg.bot.group_results, cheap=message.cheap
        )
        usage.record(session, talk.channel, chat_id, "group_find", q=message.query, results=found.total)
        if found.rows:
            usage.record(session, talk.channel, chat_id, "group_poll")
        return lang, found

    lang, found = await talk._in_db(work)
    if found is None:
        return Reply(TEXT["ask"][lang]), None
    if not found.rows:
        return Reply(say("none", lang, q=message.query)), None
    key = f"widened_{found.widened}" if found.widened else "found_one" if found.total == 1 else "found"
    shown = [replace(c, actions=()) for c in await cards(found.rows, found.extras, cfg, lang)]
    poll = (TEXT["question"][lang], [option(c.title) for c in shown] + [TEXT["nobody"][lang]])
    return Reply(say(key, lang, n=found.total), shown), poll
