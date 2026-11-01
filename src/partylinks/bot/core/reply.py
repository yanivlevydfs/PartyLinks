"""What the bot answers, independent of the chat app: text, event cards and buttons. A channel adapter
(partylinks.bot.telegram.render) draws it. Button ``action``s come back to ``conversation.press``."""

from __future__ import annotations

from dataclasses import dataclass, field

# Button actions (short: Telegram's callback data is at most 64 bytes).
MORE, ALERT, ALERT_YES, ALERT_NO, REFINE, DELETE_ALERT, SEARCH, LANG = (
    "more", "alert", "alert:yes", "alert:no", "ref:", "del:", "q:", "lang:"
)
# Under a card (``{prefix}{event number}``, refs.py) and in its ➕ menu (event_actions.py).
REMIND, WATCH, SIMILAR, OPTIONS, ICS, PLAN, FOLLOW = "rem:", "wat:", "sim:", "opt:", "ics:", "who:", "fol:"
FORGET, DIGEST = "fgt:", "dig:"  # /reminders: delete one; /digest: its choice
MENU, CONSENT = "m:", "ok:"  # the menu's screens (menu.py); the entry screen's agree / decline (consent.py)
MAX_ACTION_BYTES = 64


@dataclass(frozen=True)
class Button:
    label: str
    action: str | None = None  # pressed: sent back to the bot
    url: str | None = None  # or a link


@dataclass(frozen=True)
class Card:
    """One event: its lines in order (title first), a picture, and its link buttons."""

    event_id: str
    title: str
    lines: tuple[str, ...]
    photo_url: str | None
    buttons: tuple[Button, ...]
    actions: tuple[Button, ...] = ()  # a second row: ⏰ 👁 🔁 ➕ (none in inline mode)


@dataclass
class Reply:
    text: str
    cards: list[Card] = field(default_factory=list)
    buttons: list[list[Button]] = field(default_factory=list)  # rows, under the last message
    ask_location: bool = False  # offer the "share my location" button
    after: str | None = None  # a line after the cards (the buttons go under it)
    document: tuple[str, bytes] | None = None  # a file sent after the text: (name, content), e.g. the .ics
    photo: str | None = None  # a picture with the text as its caption (the entry screen's banner)
    html: bool = False  # ``text`` is already HTML (our own fixed texts only, never an event's)
    edit: bool = False  # a menu screen: replaces the message whose button was pressed, when it can
