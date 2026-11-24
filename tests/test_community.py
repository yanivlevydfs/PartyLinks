"""Events users post (partylinks.community): review of an account's first event, then trust; the listing as every
scraped one (enrichment, tickets, several ticket links through /go); free entry with no ticket; the form's checks;
the CAPTCHA; pictures (WebP only, no EXIF); delete, block and the sponsored-link request."""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from partylinks.api import create_app
from partylinks.community.links import BadLinkError, checked_link
from tests.test_accounts import ADMIN, FakeGoogle, _settings, sign_in

API = "/api/community/events"
ADMIN_API = "/admin/api/community"
OURS = {"X-Requested-With": "spotx"}


@pytest.fixture
def site(tmp_path: Path, fake: FakeGoogle):  # type: ignore[no-untyped-def]
    """Signed in as an ordinary user, Dana; no CAPTCHA (its own tests switch it on)."""
    settings = _settings(tmp_path)
    settings.community.captcha_required = False
    settings.media = settings.media.model_copy(update={"storage": "filesystem", "directory": tmp_path / "media"})
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        client.fake, client.settings = fake, settings  # type: ignore[attr-defined]
        sign_in(client, fake)
        yield client


def as_admin(site: TestClient) -> TestClient:
    sign_in(site, site.fake, email=ADMIN, sub="g-admin")  # type: ignore[attr-defined]
    return site


def as_dana(site: TestClient) -> TestClient:
    sign_in(site, site.fake)  # type: ignore[attr-defined]
    return site


def form(**over: Any) -> dict[str, Any]:
    day = (date.today() + timedelta(days=5)).isoformat()
    return {
        "title": "Warehouse Techno Night", "description": "A long night of techno in a warehouse by the sea.",
        "organizer": "Night Owls", "artists": ["Dj Moon"], "min_age": 21, "start_date": day, "start_time": "23:00",
        "end_time": "05:00", "doors_open": "22:30", "venue_name": "The Barby", "address": "Kibbutz Galuyot 52",
        "city": "תל אביב", "event_types": ["מסיבה"], "genres": ["techno"],
        "tickets": [
            {"name": "Early bird", "url": "https://tickets.example.com/a?x=1", "price": 80, "original_price": 120},
            {"name": "Door", "url": "https://other.example.org/b", "price": 120, "sold_out": True},
        ],
        "public_event": True,
    } | over  # fmt: skip


def listing(site: TestClient, path: str) -> dict[str, Any]:
    res = site.get(f"/events/{path.rsplit('/', 1)[-1]}")
    assert res.status_code == 200, res.text
    return res.json()


def picture(width: int = 800, height: int = 600, fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    im = Image.new("RGB", (width, height), (200, 30, 90))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make: must not survive
    im.save(out, fmt, exif=exif.tobytes()) if fmt == "JPEG" else im.save(out, fmt)
    return out.getvalue()


def test_first_event_waits_for_review_then_the_account_is_trusted(site: TestClient) -> None:
    res = site.post(API, json=form(), headers=OURS)
    assert res.status_code == 201, res.text
    first = res.json()
    assert first["status"] == "pending" and first["event_path"] is None
    assert site.get("/community/events/{}".format(first["id"]), follow_redirects=False).status_code == 410

    [queued] = as_admin(site).get(ADMIN_API, params={"status": "pending"}).json()
    assert queued["id"] == first["id"] and queued["user"]["name"] == "Dana"
    approved = site.post(f"{ADMIN_API}/{first['id']}/approve", headers=OURS).json()
    assert approved["status"] == "published" and approved["event_path"]

    event = listing(site, approved["event_path"])
    assert event["source"] == "community" and event["title"] == "Warehouse Techno Night"
    assert "techno" in event["genres"] and event["venue_name"] == "The Barby" and event["min_age"] == 21
    assert event["extra"]["doors_open"] == "22:30" and event["extra"]["no_ticket"] is False
    assert [t["url"] for t in event["extra"]["ticket_links"]] == ["https://tickets.example.com/a?x=1",
                                                                 "https://other.example.org/b"]  # fmt: skip
    assert event["min_price"] == 80  # the sold-out tier's price is not the lowest on sale
    go = site.get(f"/go/{event['id']}", params={"link": 1}, follow_redirects=False)
    assert go.status_code == 302 and go.headers["location"].startswith("https://other.example.org/b")
    first_link = site.get(f"/go/{event['id']}", params={"link": 9}, follow_redirects=False)
    assert first_link.headers["location"].startswith("https://tickets.example.com/a")

    second = as_dana(site).post(API, json=form(title="Second Night Of Techno"), headers=OURS).json()
    assert second["status"] == "published"  # trusted after one approval
    assert [e["id"] for e in site.get(API).json()] == [second["id"], first["id"]]


def test_free_entry_with_no_ticket_points_at_our_own_page(site: TestClient) -> None:
    site.settings.community.trusted_after = 0  # type: ignore[attr-defined]
    row = site.post(API, json=form(tickets=[], free_entry=True, start_time=None, end_time=None, doors_open=None),
                    headers=OURS).json()  # fmt: skip
    assert row["status"] == "published"
    event = listing(site, row["event_path"])
    assert event["entry_type"] == "free" and event["extra"]["no_ticket"] is True and event["time_known"] is False
    page = site.get(f"/community/events/{row['id']}", follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"] == row["event_path"]


@pytest.mark.parametrize(
    "over",
    [
        {"tickets": [], "free_entry": False},  # no way in
        {"tickets": [{"name": "A", "url": "http://tickets.example.com/a"}]},  # not https
        {"tickets": [{"name": "A", "url": "https://bit.ly/abc123"}]},  # a shortener
        {"tickets": [{"name": "A", "url": "https://127.0.0.1/abc123"}]},
        {"start_date": (date.today() - timedelta(days=2)).isoformat()},  # over
        {"start_date": (date.today() + timedelta(days=900)).isoformat()},
        {"genres": ["polka-trance"]},
        {"event_types": ["rave in space"]},
        {"pictures": ["0" * 32]},  # not an upload of this user
        {"description": "short"},
        {"public_event": False},
        {"wants_sponsored": True, "sponsor_phone": "03-6123456"},  # a landline
        {"country": "DE"},  # abroad needs its time zone
    ],
)
def test_bad_events_are_refused(site: TestClient, over: dict[str, Any]) -> None:
    assert site.post(API, json=form(**over), headers=OURS).status_code == 422
    assert site.get(API).json() == []


def test_signed_out_visitors_other_sites_and_bots_get_nothing_saved(site: TestClient) -> None:
    assert site.post(API, json=form()).status_code == 403  # no X-Requested-With: not our page
    assert site.post(API, json=form(website="spam.example"), headers=OURS).json()["id"] == 0
    assert site.get(API).json() == []
    site.post("/auth/logout", headers=OURS)
    site.cookies.clear()
    assert site.post(API, json=form(), headers=OURS).status_code == 401


def test_the_captcha_is_checked_on_our_server(site: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = site.settings.community  # type: ignore[attr-defined]
    cfg.captcha_required = True
    assert site.post(API, json=form(captcha_token="t"), headers=OURS).status_code == 503  # no secret set
    cfg.turnstile_secret = SecretStr("secret")
    sent: list[dict[str, Any]] = []

    def answer(success: bool) -> Any:
        def post(url: str, data: dict[str, Any], timeout: Any) -> httpx.Response:
            sent.append(data)
            return httpx.Response(200, json={"success": success}, request=httpx.Request("POST", url))

        return post

    monkeypatch.setattr(httpx, "post", answer(False))
    assert site.post(API, json=form(captcha_token="t"), headers=OURS).status_code == 400
    assert site.post(API, json=form(), headers=OURS).status_code == 400  # no token at all
    monkeypatch.setattr(httpx, "post", answer(True))
    assert site.post(API, json=form(captcha_token="good"), headers=OURS).status_code == 201
    assert sent[-1]["secret"] == "secret" and sent[-1]["response"] == "good"


def test_pictures_are_stored_as_webp_only_and_shown_on_the_listing(site: TestClient) -> None:
    res = site.post("/api/community/uploads", files={"file": ("p.jpg", picture(), "image/jpeg")}, headers=OURS)
    assert res.status_code == 201, res.text
    up = res.json()
    media = Path(site.settings.media.directory) / "community"  # type: ignore[attr-defined]
    names = sorted(p.name for p in media.iterdir())
    assert names == [f"{up['id']}.jpg.640.webp", f"{up['id']}.jpg.960.webp"]  # the original (and its EXIF) gone
    shown = site.get(up["url"])
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/webp"
    assert b"PhoneMaker" not in shown.content
    assert site.get(f"/community/uploads/{up['id']}/640.webp").status_code == 200
    assert site.get(f"/community/uploads/{up['id']}/123.webp").status_code in (404, 410)

    for bad in (picture(100, 100), b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"\xff\xd8\xff" + b"0" * 50):
        assert site.post("/api/community/uploads", files={"file": ("x.jpg", bad, "image/jpeg")},
                         headers=OURS).status_code == 422  # fmt: skip

    site.settings.community.trusted_after = 0  # type: ignore[attr-defined]
    row = site.post(API, json=form(pictures=[up["id"]]), headers=OURS).json()
    event = listing(site, row["event_path"])
    [image] = event["images"]
    stored = site.get(image["url"])
    assert stored.status_code == 200

    assert site.delete(f"{API}/{row['id']}", headers=OURS).status_code == 204
    assert site.get(f"/events/{event['id']}").status_code == 410  # gone
    assert list(media.iterdir()) == []  # its picture went with it
    assert site.get(up["url"]).status_code in (404, 410)


def test_rejecting_editing_and_blocking(site: TestClient) -> None:
    row = site.post(API, json=form(), headers=OURS).json()
    user = site.get("/api/me").json()["id"]
    admin = as_admin(site)
    rejected = admin.post(f"{ADMIN_API}/{row['id']}/reject", json={"reason": "No date on the flyer"}, headers=OURS)
    assert rejected.json()["status"] == "rejected"
    dana = as_dana(site)
    assert dana.get(f"{API}/{row['id']}").json()["reject_reason"] == "No date on the flyer"
    edited = dana.put(f"{API}/{row['id']}", json=form(title="Warehouse Techno Night, fixed"), headers=OURS).json()
    assert edited["status"] == "pending" and edited["reject_reason"] is None

    published = as_admin(site).post(f"{ADMIN_API}/{row['id']}/approve", headers=OURS).json()
    assert site.post(f"/admin/api/users/{user}/block", headers=OURS).status_code == 200
    [after] = site.get(ADMIN_API).json()
    assert after["status"] == "removed" and after["event_path"] is None
    assert site.get(f"/events/{published['event_id']}").status_code == 410  # gone


def test_a_sponsored_link_request_opens_a_promotion_request(site: TestClient) -> None:
    row = site.post(API, json=form(wants_sponsored=True, sponsor_phone="052-538-1648", sponsor_note="Home page"),
                    headers=OURS).json()  # fmt: skip
    [req] = as_admin(site).get("/admin/api/promote-requests").json()
    assert req["phone"] == "+972525381648" and req["events"][0]["id"] == f"community:{row['id']}"
    assert "Home page" in req["message"] and req["name"] == "Dana"


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("https://Tickets.Example.com/a#top", "https://tickets.example.com/a"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com:8443/x?y=1", "https://example.com:8443/x?y=1"),
    ],
)
def test_links_are_normalized(raw: str, stored: str) -> None:
    assert checked_link(raw, []) == stored


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "https://user:pw@example.com/",
        "https://localhost/x",
        "https://example.com:x/",
        "https://a b.com/",
        "https://go.bit.ly/x",
        "ftp://example.com/x",
    ],
)
def test_bad_links_are_refused(raw: str) -> None:
    with pytest.raises(BadLinkError):
        checked_link(raw, ["bit.ly"])
