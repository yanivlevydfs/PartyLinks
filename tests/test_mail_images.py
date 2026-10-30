"""Email pictures: <img> tags to JPEG crops of our own stored pictures, and the route that cuts them."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.mail import render as render_module
from partylinks.mail.render import Section, render
from tests.test_mail import Outbox, outbox  # noqa: F401 - outbox: the fixture
from tests.test_media_variants import picture, stored


def _card(eid: str, **extra: object) -> dict[str, object]:
    start = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    return {
        "id": eid,
        "slug": eid,
        "title": f"Party {eid}",
        "start_at": start,
        "city": "Tel Aviv",
        "min_price": 80,
        **extra,
    }


def test_cards_draw_img_tags_to_our_jpeg_crops_never_the_sources_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_module, "_forecasts", lambda settings, cards: {})
    settings = Settings(site={"base_url": "https://spotx.fun"})
    cards = [
        _card("a", cover_url="/images/5"),
        _card("b", cover_url="/artwork/7"),
        _card("c", image_url="https://source.example/new.jpg"),  # not downloaded yet: no picture
    ]
    _, html, _ = render(
        settings, kind="digest", lang="en", name="Dana", sections=[Section("This weekend", cards)],
        unsubscribe_url="https://spotx.fun/u", title="Your weekly update", subject="s",
    )  # fmt: skip
    assert '<img src="https://spotx.fun/images/5/mail-1104x552.jpg" width="552" height="276" alt="Party a"' in html
    assert '<img src="https://spotx.fun/artwork/7/mail-224x168.jpg" width="112" height="84" alt="Party b"' in html
    assert "source.example" not in html and "background-image" not in html and ".webp" not in html
    assert html.count("<img ") == 3  # the logo and the two stored pictures


def test_route_cuts_a_stored_picture_to_jpeg_at_the_asked_size(tmp_path: Path) -> None:
    settings, _, media, image_id = stored(tmp_path, picture((900, 1600)))  # a tall poster
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        for size in ((1104, 552), (224, 168)):
            got = client.get(f"/images/{image_id}/mail-{size[0]}x{size[1]}.jpg")
            assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
            with Image.open(io.BytesIO(got.content)) as im:
                assert im.format == "JPEG" and im.size == size
        assert media.path_for(f"_mail/i{image_id}-224x168.jpg").is_file()  # kept on disk
        again = client.get(f"/images/{image_id}/mail-224x168.jpg")
        assert again.status_code == 200 and again.content == got.content
        assert client.get(f"/images/{image_id}/mail-500x500.jpg").status_code == 404
        assert client.get("/images/999999/mail-224x168.jpg").status_code == 404


def test_digest_footer_unsubscribes_in_one_click_and_resubscribing_brings_it_back(
    tmp_path: Path,
    fake: object,
    outbox: Outbox,  # noqa: F811 - the fixture
) -> None:
    """The redesigned footer keeps every way out: the token link, the RFC 8058 headers, /alerts and /account."""
    import re

    from sqlalchemy import update

    from partylinks.accounts.models import UserRow
    from partylinks.mail import jobs, tokens
    from tests.test_accounts import OURS, sign_in
    from tests.test_mail import _app

    app, db, settings = _app(tmp_path)
    with TestClient(app) as c:
        sign_in(c, fake)
        uid = c.get("/api/me").json()["id"]
        c.patch("/api/me", json={"digest_opt_in": True, "locale": "he"}, headers=OURS)
        settings.site.base_url = "https://spotx.test"  # as in production: links are absolute https
        jobs.digest(settings, db)
        settings.site.base_url = "http://testserver"
        assert len(outbox.messages) == 1
        m = outbox.messages[0]
        found = re.search(
            r'href="(https://spotx\.test/api/email/unsubscribe\?t=([^"]+))"[^>]*>הסרה מרשימת התפוצה<', m["html"]
        )
        assert found, "footer unsubscribe link"
        url, token = found.groups()
        assert tokens.read(settings, token) == (uid, "digest")
        assert url in m["headers"]["List-Unsubscribe"]
        assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert re.search(r'href="https://spotx\.test/alerts\?[^"]*"[^>]*>ניהול ההתראות<', m["html"])
        assert re.search(r'href="https://spotx\.test/account\?[^"]*"[^>]*>הגדרות החשבון<', m["html"])

        c.cookies.clear()
        path = url.removeprefix("https://spotx.test")
        page = c.get(path)
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        with db.session() as s:
            assert s.get(UserRow, uid).digest_opt_in  # a GET (link scanners) changes nothing
        assert c.post(path, headers={"user-agent": ""}).status_code == 200  # the mail app's one-click POST
        with db.session() as s:
            user = s.get(UserRow, uid)
            assert not user.digest_opt_in and user.digest_opt_out_at is not None

        sign_in(c, fake)
        c.patch("/api/me", json={"digest_opt_in": True}, headers=OURS)
        assert c.get("/api/me").json()["digest_opt_in"]
    with db.session() as s:  # a week later
        s.execute(update(UserRow).values(last_digest_at=datetime.now(UTC) - timedelta(days=7)))
    jobs.digest(settings, db)
    assert len(outbox.messages) == 2 and outbox.messages[1]["to"] == outbox.messages[0]["to"]
