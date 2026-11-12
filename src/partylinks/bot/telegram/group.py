"""Telegram glue for friends' groups (core/group.py): the bot added or removed, ``/find`` and "@bot ..." in a
group -> the cards and one native poll. Privacy mode stays on: the bot sees its commands, mentions and
service messages only; nothing else said in the group reaches it."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from telegram import Bot, BotCommand, BotCommandScopeAllGroupChats, MessageEntity, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import Application, ChatMemberHandler, CommandHandler, ContextTypes, MessageHandler, filters

from partylinks.bot.core import group
from partylinks.bot.core.conversation import DEFAULT_LANG
from partylinks.bot.telegram import render

if TYPE_CHECKING:
    from partylinks.bot.telegram.handlers import Handlers

GROUPS = filters.ChatType.GROUPS
GONE = (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)


def install(app: Application, h: Handlers) -> None:
    app.add_handler(ChatMemberHandler(lambda u, c: _member(h, u, c)))
    app.add_handler(CommandHandler("find", lambda u, c: _find(h, u, c, " ".join(c.args or [])), filters=GROUPS))
    mention = GROUPS & filters.TEXT & filters.Entity(MessageEntity.MENTION) & ~filters.COMMAND
    app.add_handler(MessageHandler(mention, lambda u, c: _mention(h, u, c)))
    app.add_handler(MessageHandler(GROUPS & filters.StatusUpdate.MIGRATE, lambda u, c: _moved(h, u, c)))


async def describe(bot: Bot) -> None:
    """Groups' command menu: /find only (the private chat's commands are personal)."""
    await bot.set_my_commands([BotCommand(*group.COMMAND)], scope=BotCommandScopeAllGroupChats())


async def _member(h: Handlers, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if change is None or change.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat_id = str(change.chat.id)
    if change.new_chat_member.status in GONE:
        await group.leave(h.talk, chat_id)
    elif change.old_chat_member.status in GONE:  # just added (not a member made admin): the one intro
        reply = await group.join(h.talk, chat_id, change.chat.title or "", context.bot.username)
        await render.send(context.bot, chat_id, reply, DEFAULT_LANG)


async def _mention(h: Handlers, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text if update.effective_message else None
    me = re.compile(re.escape(f"@{context.bot.username}"), re.IGNORECASE)
    if text and me.search(text):  # another member mentioned: not for us
        await _find(h, update, context, me.sub(" ", text))


async def _moved(h: Handlers, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A group turned supergroup gets a new id: the old one's row goes (the new one's comes with its /find)."""
    message = update.effective_message
    if message and message.migrate_to_chat_id:
        await group.leave(h.talk, str(message.chat_id))


async def _find(h: Handlers, update: Update, context: ContextTypes.DEFAULT_TYPE, words: str) -> None:
    chat = update.effective_chat
    if chat is None or h.flood.check(str(chat.id), h.settings().bot.messages_per_minute) != "ok":
        return  # over the limit: silence, a "wait" would only add to a busy group
    await context.bot.send_chat_action(chat.id, "typing")
    reply, poll = await group.find(h.talk, str(chat.id), words)
    await render.send(context.bot, chat.id, reply, DEFAULT_LANG)
    if poll:
        await context.bot.send_poll(chat.id, poll[0], poll[1], is_anonymous=False, allows_multiple_answers=False)
