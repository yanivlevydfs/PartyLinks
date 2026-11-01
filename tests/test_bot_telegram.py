"""The Telegram adapter without Telegram: rendering a Reply, the webhook's secret, the alert job
(match, send, no event twice, a blocked chat). A fake bot records what would be sent."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from telegram import Bot, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.error import BadRequest, Forbidden, TimedOut

from partylinks.alerts import matcher
from partylinks.api import create_app
from partylinks.bot.core import alerts, forecast
from partylinks.bot.core.reply import Button, Card, Reply
from partylinks.bot.models import BotAlertHitRow, BotAlertRow, BotChatRow
from partylinks.bot.telegram import jobs, render, webhook
from partylinks.bot.telegram.webhook import PATH, SECRET_HEADER
from partylinks.config import Settings
from partylinks.db import EventRow
from tests.test_storage_api import event, seed, settings_for

SOON = datetime.now(UTC) + timedelta(days=2)
SECRET = "s3cret-token_for-tests"


class FakeBot:
    def __init__(self, refuse_photos: bool = False, forbidden: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.refuse_photos, self.forbidden = refuse_photos, forbidden

    async def send_message(self, chat_id: Any, text: str, **kw: Any) -> None:
        if self.forbidden:
            raise Forbidden("bot was blocked by the user")
        self.sent.append(("text", {"chat_id": chat_id, "text": text, **kw}))

    async def send_photo(self, chat_id: Any, photo: str, **kw: Any) -> None:
        if self.refuse_photos:
            raise BadRequest("wrong type of the web page content")
        self.sent.append(("photo", {"chat_id": chat_id, "photo": photo, **kw}))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_forecast(*args: Any, **kwargs: Any) -> list[list[Any]]:
        return []

    monkeypatch.setattr(forecast.weather, "forecast", no_forecast)
    monkeypatch.setattr(matcher, "SETTLE", timedelta(0))


CARD = Card("test:a", "Techno <Night> & more", ("🗓 Fri", "📍 Barby"), "https://x/images/1/mail-1104x552.jpg",
            (Button("🎟", url="https://x/go/a?at=tg"),))  # fmt: skip


def test_a_reply_is_intro_cards_then_buttons() -> None:
    bot = FakeBot()
    reply = Reply("Found 1", [CARD], [[Button("More", "more")]], after="Next?")
    asyncio.run(render.send(bot, 5, reply, "en"))  # type: ignore[arg-type]
    kinds = [k for k, _ in bot.sent]
    assert kinds == ["text", "photo", "text"]
    photo = bot.sent[1][1]
    assert "<b>Techno &lt;Night&gt; &amp; more</b>" in photo["caption"]  # event text is escaped
    assert isinstance(photo["reply_markup"], InlineKeyboardMarkup)
    assert bot.sent[2][1]["reply_markup"].inline_keyboard[0][0].callback_data == "more"


def test_a_picture_telegram_cannot_fetch_becomes_a_text_card() -> None:
    bot = FakeBot(refuse_photos=True)
    asyncio.run(render.send(bot, 5, Reply("x", [CARD]), "he"))  # type: ignore[arg-type]
    assert [k for k, _ in bot.sent] == ["text", "text"]
    assert "Techno" in bot.sent[1][1]["text"]


def test_asking_for_the_location_shows_the_location_button() -> None:
    bot = FakeBot()
    asyncio.run(render.send(bot, 5, Reply("share", ask_location=True), "he"))  # type: ignore[arg-type]
    markup = bot.sent[0][1]["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup) and markup.keyboard[0][0].request_location


def test_inline_results_have_ids_telegram_accepts() -> None:
    results = render.inline_results([CARD, Card("x" * 300, "No picture", ("a",), None, ())])
    assert all(len(r.id.encode()) <= 64 for r in results)
    assert [type(r).__name__ for r in results] == ["InlineQueryResultPhoto", "InlineQueryResultArticle"]


# ---------------------------------------------------------------------- webhook
def _bot_settings(tmp_path: Path) -> Settings:
    s = settings_for(tmp_path)
    return s.model_copy(update={"bot": s.bot.model_copy(update={"telegram_token": SecretStr("123:abc"), "telegram_webhook_secret": SecretStr(SECRET)})})


class _Running:
    """What webhook.start returns, without Telegram: a bot to read updates with, and the queue."""

    def __init__(self) -> None:
        self.bot = Bot("123:abc")
        self.update_queue: asyncio.Queue[Any] = asyncio.Queue()

    async def stop(self) -> None: ...

    async def shutdown(self) -> None: ...


def test_the_webhook_takes_updates_only_with_the_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    running = _Running()

    async def start(app: Any) -> _Running:
        return running

    monkeypatch.setattr(webhook, "start", start)
    settings = _bot_settings(tmp_path)
    seed(settings, [])
    update = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 7, "type": "private"}, "text": "hi"}}
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.post(PATH, json=update).status_code == 403  # no secret (and no user agent: that alone is let in)
        assert c.post(PATH, json=update, headers={SECRET_HEADER: "wrong"}).status_code == 403
        assert c.post(PATH, content=b"not json", headers={SECRET_HEADER: SECRET}).status_code == 400
        for _ in range(3):  # Telegram's servers: never rate limited or banned by the site's guard
            assert c.post(PATH, json=update, headers={SECRET_HEADER: SECRET}).status_code == 200
    assert running.update_queue.qsize() == 3


def test_without_secrets_there_is_no_webhook(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    seed(settings, [])
    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.post(PATH, json={}, headers={SECRET_HEADER: "x", "user-agent": "t"}).status_code == 404


# ---------------------------------------------------------------------- alerts job
def _chat_with_alert(db: Any, query: str, chat_id: str = "42") -> int:
    with db.session() as s:
        chat = BotChatRow(channel="telegram", chat_id=chat_id, lang="en", last_offset=0, last_cheap=False)
        s.add(chat)
        s.flush()
        s.add(BotAlertRow(chat_ref=chat.id, query=query))
        return chat.id


def test_new_matching_events_are_sent_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    assert alerts.match(settings, db) == 0  # the first pass only sets the cursor
    _chat_with_alert(db, "techno")
    seed(settings, [event("t1", SOON, title="Techno One"), event("j1", SOON, title="Jazz One")])
    assert alerts.match(settings, db) == 1
    bot = FakeBot()
    assert asyncio.run(jobs.send_due(settings, db, bot)) == 1  # type: ignore[arg-type]
    assert "techno" in bot.sent[0][1]["text"] and len(bot.sent) == 2  # the header and one card
    assert alerts.match(settings, db) == 0
    assert asyncio.run(jobs.send_due(settings, db, FakeBot())) == 0  # type: ignore[arg-type]


def test_an_event_matching_two_alerts_is_sent_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    alerts.match(settings, db)
    chat = _chat_with_alert(db, "techno")
    with db.session() as s:
        s.add(BotAlertRow(chat_ref=chat, query="garden"))
    seed(settings, [event("g", SOON, title="Techno Garden")])
    assert alerts.match(settings, db) == 2
    bot = FakeBot()
    asyncio.run(jobs.send_due(settings, db, bot))  # type: ignore[arg-type]
    assert sum(k == "photo" or "Techno Garden" in v.get("text", "") for k, v in bot.sent) == 1
    assert asyncio.run(jobs.send_due(settings, db, FakeBot())) == 0  # type: ignore[arg-type]


def test_a_blocked_chat_is_marked_and_skipped(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    alerts.match(settings, db)
    _chat_with_alert(db, "techno")
    seed(settings, [event("t1", SOON, title="Techno One")])
    alerts.match(settings, db)
    assert asyncio.run(jobs.send_due(settings, db, FakeBot(forbidden=True))) == 0  # type: ignore[arg-type]
    with db.session(write=False) as s:
        assert s.scalar(select(BotChatRow.blocked_at)) is not None
    assert alerts.due(settings, db) == []


def test_hits_of_events_that_are_over_are_pruned(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    alerts.match(settings, db)
    _chat_with_alert(db, "techno")
    seed(settings, [event("t1", SOON, title="Techno One")])
    alerts.match(settings, db)
    with db.session() as s:
        s.execute(update(EventRow).values(is_listed=False))
    assert alerts.prune(db) == 1


def test_a_short_secret_keeps_the_bot_off(tmp_path: Path) -> None:
    s = settings_for(tmp_path)
    for secret in ("", "short"):
        cfg = s.model_copy(update={"bot": s.bot.model_copy(update={"telegram_token": SecretStr("1:a"), "telegram_webhook_secret": SecretStr(secret)})})

        class App:
            class state:
                settings = cfg

        assert asyncio.run(webhook.start(App())) is None  # type: ignore[arg-type]
        assert not webhook.strong(cfg.bot.telegram_webhook_secret)


def test_guessing_the_secret_ends_in_a_ban(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def start(app: Any) -> _Running:
        return _Running()

    monkeypatch.setattr(webhook, "start", start)
    settings = _bot_settings(tmp_path)
    seed(settings, [])
    with TestClient(create_app(settings, start_scheduler=False), client=("198.51.100.30", 1)) as c:
        codes = [c.post(PATH, json={}, headers={SECRET_HEADER: f"guess-{i}", "user-agent": "x"}).status_code for i in range(12)]
    assert codes[0] == 403 and codes[-1] == 403
    assert c.app.state.guard.is_banned("198.51.100.30", time.monotonic())  # type: ignore[attr-defined]


def test_an_alert_cut_short_is_not_sent_again(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    alerts.match(settings, db)
    _chat_with_alert(db, "techno")
    seed(settings, [event("t1", SOON, title="Techno One")])
    alerts.match(settings, db)

    class Flaky(FakeBot):
        async def send_photo(self, chat_id: Any, photo: str, **kw: Any) -> None:
            raise TimedOut()

    asyncio.run(jobs.send_due(settings, db, Flaky()))  # type: ignore[arg-type]
    assert alerts.due(settings, db) == []  # the intro went out: the next pass does not start it over


def test_matches_not_sent_within_a_day_are_dropped(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = seed(settings, [])
    alerts.match(settings, db)
    _chat_with_alert(db, "techno")
    seed(settings, [event("t1", SOON, title="Techno One")])
    alerts.match(settings, db)
    with db.session() as s:
        s.execute(update(BotAlertHitRow).values(matched_at=datetime.now(UTC) - timedelta(days=2)))
    assert alerts.prune(db) == 1
    assert alerts.due(settings, db) == []
