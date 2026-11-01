"""The entry screen (consent.py) and the menu (menu.py): nothing is kept before "I agree", a silent chat is
deleted, every menu choice is words the site's search reads, and every button fits Telegram's 64 bytes."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from partylinks.bot.core import consent, menu
from partylinks.bot.core.reply import MAX_ACTION_BYTES, MENU
from partylinks.bot.models import BotChatRow, BotUsageRow
from partylinks.db import utcnow
from partylinks.search import ABROAD, DAYS, WEEKEND, _vocabulary, date_ranges
from tests.test_bot_features import CHAT, _talk
from tests.test_storage_api import settings_for

CAPTION_LIMIT = 1024  # Telegram: a photo's caption, after its HTML tags are read


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _chats(db: Any) -> int:
    with db.session() as session:
        return session.scalar(select(func.count()).select_from(BotChatRow)) or 0


def test_nothing_is_kept_before_i_agree(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    screen = _run(talk.gate(CHAT))
    assert screen is not None and screen.photo and screen.html
    assert [b.action for row in screen.buttons for b in row] == [consent.AGREE, consent.DECLINE]
    assert _chats(db) == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(BotUsageRow)) == 0

    welcome = _run(talk.press(CHAT, consent.AGREE, "he"))
    assert welcome.photo and any(b.action == menu.action("g") for row in welcome.buttons for b in row)
    assert _run(talk.gate(CHAT)) is None
    assert _chats(db) == 1


def test_decline_deletes_the_chat(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    _run(talk.press(CHAT, consent.AGREE, "he"))
    _run(talk.text(CHAT, "טכנו", "he"))
    reply = _run(talk.press(CHAT, consent.DECLINE, "he"))
    assert "/start" in reply.text
    assert _chats(db) == 0
    assert _run(talk.gate(CHAT)) is not None


def test_a_silent_chat_is_deleted(tmp_path: Path) -> None:
    talk, db, settings = _talk(tmp_path)
    _run(talk.press(CHAT, consent.AGREE, "he"))
    _run(talk.press("other", consent.AGREE, "he"))
    with db.session() as session:
        old = utcnow() - timedelta(days=settings.bot.inactive_days + 1)
        session.execute(update(BotChatRow).where(BotChatRow.chat_id == CHAT).values(seen_at=old))
    assert consent.prune(settings, db) == 1
    with db.session() as session:
        assert session.scalars(select(BotChatRow.chat_id)).all() == ["other"]


def test_the_entry_screen_fits_a_caption(tmp_path: Path) -> None:
    text = consent.text(settings_for(tmp_path))
    assert len(re.sub(r"<[^>]+>", "", text)) <= CAPTION_LIMIT


def test_every_menu_choice_is_words_the_search_reads(tmp_path: Path) -> None:
    cfg = settings_for(tmp_path)
    vocab = _vocabulary(cfg)
    dates = {*WEEKEND, *DAYS, *date_ranges(utcnow().date())}
    genres = [*cfg.enrichment.genre_groups, *(g for gs in cfg.enrichment.genre_groups.values() for g in gs)]
    for lang in ("he", "en"):
        for genre in genres:
            assert menu.genre_words(genre, lang).casefold() in vocab, (genre, lang)
        for area, words in menu.AREAS.items():
            word = words[0 if lang == "he" else 1].casefold()
            assert word in vocab or word in ABROAD, (area, lang)
        for when, (words, _) in menu.WHEN.items():
            assert words[0 if lang == "he" else 1].casefold() in dates, (when, lang)


def test_every_menu_button_fits(tmp_path: Path) -> None:
    cfg = settings_for(tmp_path)
    genres = [*cfg.enrichment.genre_groups, *(g for gs in cfg.enrichment.genre_groups.values() for g in gs)]
    longest = max(genres, key=len)
    for screen in "hgGawr":
        assert len(menu.action(screen, longest, max(menu.AREAS, key=len), "w7").encode()) <= MAX_ACTION_BYTES
    replies = [menu.home(cfg, "he"), menu.groups(cfg, "he", "", ""), menu.areas("he", "", ""), menu.dates("he", "", "")]
    replies += [menu.group(cfg, "he", g, "galilee", "wk") for g in cfg.enrichment.genre_groups]
    for reply in replies:
        for row in reply.buttons:
            assert all(b.action and len(b.action.encode()) <= MAX_ACTION_BYTES for b in row)


def test_a_menu_choice_finds_events_and_keeps_the_choice(tmp_path: Path) -> None:
    talk, _, _ = _talk(tmp_path)
    _run(talk.press(CHAT, consent.AGREE, "he"))
    reply = _run(talk.press(CHAT, menu.action("r", "techno"), "he"))
    assert reply.cards and all("Techno" in c.title or "Rave" in c.title for c in reply.cards)
    actions = [b.action or "" for row in reply.buttons for b in row]
    assert menu.action("a", "techno", "", "") in actions  # change the area, keep techno
    assert not any(a.startswith("ref:") for a in actions)
    screen = _run(talk.press(CHAT, menu.action("a", "techno"), "he"))
    assert screen.edit and any(b.action == menu.action("r", "techno", "galilee", "") for row in screen.buttons for b in row)
    switched = _run(talk.press(CHAT, MENU + "lang", "he"))
    assert switched.text.startswith("I'll speak English")


def test_the_entry_screen_is_one_picture_with_its_buttons(tmp_path: Path) -> None:
    from partylinks.bot.telegram import render
    from tests.test_bot_features import FakeBot

    bot = FakeBot()
    _run(render.send(bot, CHAT, consent.screen(settings_for(tmp_path)), "he"))  # type: ignore[arg-type]
    assert [kind for kind, _ in bot.sent] == ["photo"]
    sent = bot.sent[0][1]
    assert sent["photo"].endswith("/bot-banner-he.png") and "<b>" in sent["caption"] and sent["reply_markup"]
