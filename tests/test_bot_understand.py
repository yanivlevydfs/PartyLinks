"""The bot reads chat messages right: real sentences -> what it searches (partylinks.bot.core.understand).
Every message the bot gets wrong in production becomes a row here."""

from __future__ import annotations

from datetime import date

import pytest

from partylinks.bot.core.understand import Message, clean, lang_of, read
from partylinks.config import Settings

TODAY = date(2026, 11, 1)  # a Sunday


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(scheduler={"enabled": False})


# (message, the previous search or None, what the bot should search)
SEARCHES = [
    ("טכנו בתל אביב בסופ״ש", None, 'טכנו בתל אביב בסופ״ש'),
    ('טכנו בסופ"ש', None, 'טכנו בסופ"ש'),
    ("מה יש הערב בתל אביב?", None, "הערב בתל אביב"),
    ("תמצא לי מסיבת טבע בצפון עד 100 ש״ח", None, "מסיבת טבע בצפון עד 100 ש״ח"),
    ("בא לי הופעה בחיפה מחר", None, "הופעה בחיפה מחר"),
    ("אני מחפש משהו חינם הערב", None, "חינם הערב"),
    ("סטנד אפ ביום שישי", None, "סטנד אפ ביום שישי"),
    ("ג'אז בירושלים השבוע", None, "ג'אז בירושלים השבוע"),
    ("הופעות בחו״ל בדצמבר", None, "הופעות בחו״ל בדצמבר"),
    ("Infected Mushroom", None, "infected mushroom"),
    ("ויני ויצ'י בחיפה", "טכנו", "ויני ויצ'י בחיפה"),  # a name starting with ו, not "and in Haifa"
    ("jazz tonight", None, "jazz tonight"),
    ("free events this weekend", None, "free this weekend"),
    ("what's on in berlin this weekend?", None, "berlin this weekend"),
    ("drum and bass", None, "drum and bass"),
    ("jazz in may", None, "jazz in may"),
    # follow-ups: the part named replaces the previous one
    ("ומחר?", "טכנו בסופ״ש", "טכנו מחר"),
    ("ובחיפה", "טכנו בתל אביב הערב", "טכנו הערב בחיפה"),
    ("ומה עם הצפון?", "מסיבת טבע במרכז", "מסיבת טבע הצפון"),
    ("ביום שישי", "טכנו הערב", "טכנו ביום שישי"),
    ("and in haifa", "jazz tonight in tel aviv", "jazz tonight haifa"),
    ("what about tomorrow", "jazz tonight", "jazz tomorrow"),
    ("in march", "jazz", "jazz in march"),
    ("ועד 80", "טכנו עד 150", "טכנו עד 80"),
]


@pytest.mark.parametrize(("text", "previous", "query"), SEARCHES)
def test_a_message_searches_its_words(cfg: Settings, text: str, previous: str | None, query: str) -> None:
    message = read(text, cfg, previous, TODAY)
    assert message.kind == "search"
    assert message.query == query
    assert message.follow_up == (previous is not None and query != clean(text, 200))


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("שלום!", "greeting"), ("היי 👋", "greeting"), ("hello", "greeting"), ("מה יש?", "greeting"), ("", "empty"),
        ("👍", "empty"), ("תודה רבה!", "thanks"), ("thanks", "thanks"), ("עזרה", "help"), ("❓ עזרה", "help"),
        ("עוד", "more"), ("more", "more"), ("🔔 ההתראות שלי", "alerts"), ("📍 קרוב אליי", "near"), ("near me", "near"),
    ],
)  # fmt: skip
def test_chat_words_are_answered_not_searched(cfg: Settings, text: str, kind: str) -> None:
    assert read(text, cfg, None, TODAY).kind == kind


def test_cheapest_and_near_are_options_not_words(cfg: Settings) -> None:
    assert read("טכנו הכי זול", cfg, None, TODAY) == Message("search", "טכנו", cheap=True)
    assert read("הכי זול", cfg, "טכנו בחיפה", TODAY) == Message("search", "טכנו בחיפה", cheap=True, follow_up=True)
    assert read("jazz near me", cfg, None, TODAY) == Message("search", "jazz", near=True)


def test_a_new_search_is_not_a_follow_up(cfg: Settings) -> None:
    assert read("jazz tonight", cfg, "techno in haifa", TODAY) == Message("search", "jazz tonight")
    assert read("וטכנו", cfg, "jazz", TODAY).follow_up is False  # not a date, place or price: a search of its own


def test_long_messages_are_cut(cfg: Settings) -> None:
    assert len(read("טכנו " * 200, cfg, None, TODAY).query) <= cfg.bot.max_text_length


def test_language_follows_the_letters() -> None:
    assert lang_of("טכנו", "en") == "he"
    assert lang_of("techno", "he") == "en"
    assert lang_of("100 🎉", "en") == "en"
