"""🗺 SPOTX inside Telegram: the menu's web_app row and the chat's menu button open site.base_url/?src=tg-app
(over https only), and the site's headers let Telegram Web frame it and load Telegram's script."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from telegram import MenuButtonCommands, MenuButtonWebApp

from partylinks.api import create_app
from partylinks.bot.core import menu, webapp
from partylinks.bot.telegram import handlers, render
from tests.test_storage_api import settings_for

SITE = "https://spotx.fun"


class Recorder:
    """A bot that records every call (describe only sets things)."""

    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        async def call(*args: Any, **kw: Any) -> None:
            self.calls[name] = kw

        return call


def _settings(tmp_path: Path, base: str) -> Any:
    settings = settings_for(tmp_path)
    settings.site.base_url = base
    return settings


def test_menu_row_opens_the_site_as_a_web_app(tmp_path: Path) -> None:
    for lang in ("he", "en"):
        rows = menu.home(_settings(tmp_path, SITE + "/"), lang).buttons
        markup = render.keyboard(rows)
        assert markup is not None
        apps = [b for row in markup.inline_keyboard for b in row if b.web_app]
        assert [(b.text, b.web_app.url) for b in apps] == [(webapp.TEXT[lang], SITE + "/?src=tg-app")]
    plain = menu.home(_settings(tmp_path, "http://localhost:8000"), "he").buttons
    assert not any(b.web_app for row in plain for b in row)  # Telegram refuses a non-https Web App


def test_describe_sets_the_menu_button(tmp_path: Path) -> None:
    bot = Recorder()
    asyncio.run(handlers.describe(SimpleNamespace(bot=bot), _settings(tmp_path, SITE)))  # type: ignore[arg-type]
    button = bot.calls["set_chat_menu_button"]["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert (button.text, button.web_app.url) == (webapp.MENU_BUTTON, SITE + "/?src=tg-app")
    asyncio.run(handlers.describe(SimpleNamespace(bot=bot), _settings(tmp_path, "http://localhost:8000")))  # type: ignore[arg-type]
    assert isinstance(bot.calls["set_chat_menu_button"]["menu_button"], MenuButtonCommands)


def test_only_the_web_app_entry_page_may_be_framed_by_telegram_web(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path), start_scheduler=False)) as client:
        csp = {u: client.get(u).headers["content-security-policy"] for u in ("/?src=tg-app", "/", "/admin?src=tg-app")}
    assert "frame-ancestors https://web.telegram.org;" in csp["/?src=tg-app"] + ";"
    assert "frame-ancestors 'none'" in csp["/"] and "frame-ancestors 'none'" in csp["/admin?src=tg-app"]
    assert "https://telegram.org" in next(d for d in csp["/"].split("; ") if d.startswith("script-src"))
