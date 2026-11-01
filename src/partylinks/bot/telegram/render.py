"""A core Reply -> Telegram messages: the intro, one photo message per event card (its links as buttons),
then a line with the reply's buttons. HTML parse mode; every text from events is escaped."""

from __future__ import annotations

import hashlib
import logging
from html import escape

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InputFile,
    InputTextMessageContent,
    KeyboardButton,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
)  # fmt: skip
from telegram.constants import ParseMode
from telegram.error import BadRequest

from partylinks.bot.core.phrases import label
from partylinks.bot.core.reply import Button, Card, Reply

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def keyboard(rows: list[list[Button]]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b.label, url=b.url) if b.url else InlineKeyboardButton(b.label, callback_data=b.action) for b in row]
        for row in rows
    ])  # fmt: skip


def main_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """The buttons under the typing box: share location, tonight, this weekend, my alerts, help."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(label("near", lang), request_location=True)],
            [KeyboardButton(label("tonight", lang)), KeyboardButton(label("weekend", lang))],
            [KeyboardButton(label("my_alerts", lang)), KeyboardButton(label("my_events", lang))],
            [KeyboardButton(label("help", lang))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


TITLE_LIMIT = 300  # with the short card lines, well inside a caption's 1024 characters


def caption(card: Card) -> str:
    title = card.title if len(card.title) <= TITLE_LIMIT else card.title[: TITLE_LIMIT - 1] + "…"
    return f"<b>{escape(title)}</b>\n" + "\n".join(escape(line) for line in card.lines)


async def send_card(bot: Bot, chat_id: int | str, card: Card) -> None:
    markup = keyboard([row for row in (list(card.buttons), list(card.actions)) if row])
    if card.photo_url:
        try:
            await bot.send_photo(chat_id, card.photo_url, caption=caption(card), parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        except BadRequest as exc:  # Telegram could not fetch the picture: the card as text
            log.info("bot: photo of %s not sent (%s), sent as text", card.event_id, exc.message)
    await bot.send_message(chat_id, caption(card), parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW)


async def send(bot: Bot, chat_id: int | str, reply: Reply, lang: str) -> None:
    """Every message of ``reply``, in order. With no cards the buttons go under the text."""
    under_text = None if reply.cards else keyboard(reply.buttons)
    markup = main_keyboard(lang) if reply.ask_location else under_text
    await bot.send_message(chat_id, escape(reply.text), parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW)
    if reply.ask_location and under_text:
        await bot.send_message(chat_id, "👇", reply_markup=under_text)
    if reply.document:
        name, content = reply.document
        await bot.send_document(chat_id, InputFile(content, filename=name))
    for card in reply.cards:
        await send_card(bot, chat_id, card)
    if reply.cards and (reply.after or reply.buttons):
        await bot.send_message(chat_id, escape(reply.after or "👇"), parse_mode=ParseMode.HTML, reply_markup=keyboard(reply.buttons))


def inline_results(found: list[Card]) -> list[InlineQueryResultPhoto | InlineQueryResultArticle]:
    """Cards to pick in "@bot ..." in any chat: a photo with its caption, or text when there is no picture."""
    out: list[InlineQueryResultPhoto | InlineQueryResultArticle] = []
    for card in found:
        result_id = hashlib.sha1(card.event_id.encode()).hexdigest()  # at most 64 bytes
        markup = keyboard([list(card.buttons)])
        if card.photo_url:
            out.append(InlineQueryResultPhoto(
                result_id, card.photo_url, card.photo_url, title=card.title, description=" · ".join(card.lines[:2]),
                caption=caption(card), parse_mode=ParseMode.HTML, reply_markup=markup,
            ))  # fmt: skip
        else:
            out.append(InlineQueryResultArticle(
                result_id, card.title, InputTextMessageContent(caption(card), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW),
                description=" · ".join(card.lines[:2]), reply_markup=markup,
            ))  # fmt: skip
    return out
