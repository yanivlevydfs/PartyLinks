"""➕ 📍 under a card: a map pin with Waze / Google Maps, and a place's hours, phone and Ontopo booking."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sqlalchemy import update

from partylinks.bot.core.reply import OPTIONS
from partylinks.bot.telegram import render
from partylinks.db import EventRow
from partylinks.placeinfo.models import OntopoVenueRow, PlaceInfoRow
from tests.test_bot_features import CHAT, FakeBot, _action, _card, _run, _talk


class VenueBot(FakeBot):
    async def send_venue(self, chat_id: Any, *args: Any, **kw: Any) -> None:
        self.sent.append(("venue", {"chat_id": chat_id, "args": args}))


def _navigate(talk: Any, title: str) -> Any:
    menu = _run(talk.press(CHAT, _action(_card(talk, title), OPTIONS), "en"))
    nav = next(b.action for row in menu.buttons for b in row if b.action and b.action.startswith("nav:"))
    return _run(talk.press(CHAT, nav, "en"))


def test_a_pin_with_waze_and_google_maps_then_place_info(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    with db.session() as s:
        s.execute(update(EventRow).where(EventRow.title == "Techno Bunker").values(latitude=32.06, longitude=34.77))
        url = s.query(EventRow.url).filter(EventRow.title == "Techno Bunker").scalar()
        s.add(OntopoVenueRow(slug="block", phone="037943529", hours={"Sunday": ["20:00-03:00"]}))
        s.add(PlaceInfoRow(url=url, ontopo_slug="block"))
    reply = _navigate(talk, "Techno Bunker")
    urls = [b.url for row in reply.buttons for b in row]
    assert "https://waze.com/ul?ll=32.06,34.77&navigate=yes" in urls
    assert "https://www.google.com/maps/dir/?api=1&destination=32.06,34.77" in urls
    assert "https://ontopo.com/en/il/page/block?source=spotx" in urls
    assert "+97237943529" in reply.text and "Sunday: 20:00-03:00" in reply.text and "Monday: Closed" in reply.text
    bot = VenueBot()
    _run(render.send(cast(Any, bot), CHAT, reply, "en"))
    assert bot.sent[0] == ("venue", {"chat_id": CHAT, "args": (32.06, 34.77, "The Block", "Tel Aviv-Yafo, Israel")})


def test_no_coordinates_says_so_with_the_address_and_no_place_part(tmp_path: Path) -> None:
    talk, db, _ = _talk(tmp_path)
    with db.session() as s:
        s.execute(update(EventRow).where(EventRow.title == "Jazz Club Night").values(latitude=None, longitude=None))
    reply = _navigate(talk, "Jazz Club Night")
    assert "exact location" in reply.text and "Jerusalem, Israel" in reply.text
    assert reply.venue is None and not reply.buttons and "hours" not in reply.text
