from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr

from partylinks import seo
from partylinks.admin import _mask
from partylinks.api import create_app
from partylinks.config import Settings
from partylinks.db import EventRow
from tests.test_storage_api import event, seed, settings_for


def test_headers_probes_bots_and_bans(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.ban_after_strikes = 3
    settings.security.rate_limit_per_minute = 5
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as local, TestClient(app, client=("203.0.113.7", 5000)) as outsider:
        ok = local.get("/health")
        assert ok.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in ok.headers["content-security-policy"]
        assert "server" not in ok.headers
        assert local.get("/docs").status_code == 404 and local.get("/openapi.json").status_code == 404

        assert outsider.get("/events", headers={"user-agent": "sqlmap/1.7"}).status_code == 404
        assert outsider.get("/", headers={"user-agent": "Mozilla/5.0 (compatible; AhrefsBot/7.0)"}).status_code == 403
        assert outsider.get("/robots.txt", headers={"user-agent": "AhrefsBot"}).status_code == 200
        assert "User-agent: AhrefsBot" in local.get("/robots.txt").text

        assert outsider.get("/.env").status_code == 404
        assert outsider.get("/wp-login.php?x=1").status_code == 404  # third strike: banned
        assert outsider.get("/health").status_code == 403
        assert local.get("/health").status_code == 200  # this machine is never banned


def test_rate_limit_and_admin_brute_force(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = 3
    settings.security.ban_after_strikes = 2
    settings.admin.token = SecretStr("s3cret")
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, client=("198.51.100.1", 1)) as a, TestClient(app, client=("198.51.100.2", 1)) as b:
        assert [a.get("/health").status_code for _ in range(4)] == [200, 200, 200, 429]
        assert {b.get(f"/images/{i}").status_code for i in range(5)} == {404}  # artwork: not limited
        assert {b.get(f"/images/{i}/640.webp").status_code for i in range(5)} == {404}  # nor its WebP copies
        # The admin page sends one token on several requests at once: a repeated typo is one strike.
        assert [b.get("/admin/api/overview", headers={"authorization": "Bearer nope"}).status_code for _ in range(5)] == [401] * 5
        assert b.get("/admin/api/overview", headers={"authorization": "Bearer nope2"}).status_code == 401
        assert b.get("/admin/api/overview", headers={"authorization": "Bearer s3cret"}).status_code == 403  # banned
        page = b.get("/", headers={"accept": "text/html,*/*"})  # a person sees words, not JSON
        assert page.status_code == 403 and "text/html" in page.headers["content-type"] and "נחסמה" in page.text


def test_owner_is_not_rate_limited_and_lifts_bans(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.rate_limit_per_minute = 3
    settings.security.ban_after_strikes = 2
    settings.admin.token = SecretStr("s3cret")
    owner = {"authorization": "Bearer s3cret"}
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, client=("198.51.100.5", 1)) as me, TestClient(app, client=("198.51.100.6", 1)) as other:
        assert {me.get("/health", headers=owner).status_code for _ in range(10)} == {200}  # the owner's checks
        assert [other.get("/health").status_code for _ in range(6)] == [200, 200, 200, 429, 429, 403]  # 2 strikes: banned
        assert other.get("/health", headers=owner).status_code == 403  # the token does not open a ban (no guessing)
        assert me.get("/admin/api/bans", headers=owner).json().keys() == {"198.51.100.6"}
        assert me.post("/admin/api/bans/lift?ip=198.51.100.6", headers=owner).json() == {"lifted": ["198.51.100.6"]}
        assert other.get("/health").status_code == 200 and me.get("/admin/api/bans", headers=owner).json() == {}
        assert me.post("/admin/api/bans/lift", headers={"authorization": "Bearer nope"}).status_code == 401
        assert me.post("/admin/api/bans/lift", headers={"authorization": "Bearer nope2"}).status_code == 401
        assert me.get("/health", headers=owner).status_code == 403  # two wrong tokens: banned, the token or not


def test_cache_and_compression(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    settings.security.ban_after_strikes = 1
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as local, TestClient(app, client=("198.51.100.3", 1)) as outsider:
        events = local.get("/events?size=100", headers={"accept-encoding": "gzip"})
        assert events.headers["cache-control"].startswith("public, max-age=30")
        assert "cache-control" not in local.get("/health").headers  # monitors always see live state
        assert local.get("/admin/api/overview", headers={"authorization": "Bearer s3cret"}).headers["cache-control"] == "no-store"
        # A wrong token on an admin-only API route counts as a strike too, not only under /admin.
        assert outsider.post("/maintenance/retention", headers={"authorization": "Bearer nope"}).status_code == 401
        assert outsider.get("/health").status_code == 403


def test_spoofed_forwarded_for_is_ignored(tmp_path: Path) -> None:
    """Behind a proxy (trusted_proxies ["*"]) the visitor writes the first X-Forwarded-For entry: a
    fake 127.0.0.1 there must not make the request local (no limits, no bans)."""
    settings = settings_for(tmp_path)
    settings.security.trusted_proxies = ["*"]
    settings.security.rate_limit_per_minute = 2
    app = create_app(settings, start_scheduler=False)
    spoof = {"x-forwarded-for": "127.0.0.1, 198.51.100.9"}
    with TestClient(app, client=("10.0.0.2", 1)) as proxy:
        assert [proxy.get("/health", headers=spoof).status_code for _ in range(3)] == [200, 200, 429]
        assert proxy.get("/admin/api/overview", headers=spoof).status_code == 403  # no token: not local


def test_cloudflare_visitors_are_told_apart(tmp_path: Path) -> None:
    """Behind Cloudflare, Railway's X-Real-IP is Cloudflare's edge: limits go by CF-Connecting-IP,
    which counts only when the request really came through Cloudflare."""
    settings = settings_for(tmp_path)
    settings.security.trusted_proxies = ["*"]
    settings.security.rate_limit_per_minute = 1
    app = create_app(settings, start_scheduler=False)
    edge = {"x-real-ip": "172.70.1.1"}  # one Cloudflare edge address for everyone
    with TestClient(app, client=("10.0.0.2", 1)) as railway:
        assert railway.get("/health", headers=edge | {"cf-connecting-ip": "198.51.100.30"}).status_code == 200
        assert railway.get("/health", headers=edge | {"cf-connecting-ip": "198.51.100.31"}).status_code == 200
        assert railway.get("/health", headers=edge | {"cf-connecting-ip": "198.51.100.31"}).status_code == 429
        # Straight to Railway, a made-up CF-Connecting-IP changes nothing: still the sender's address.
        direct = {"x-real-ip": "203.0.113.50"}
        assert railway.get("/health", headers=direct | {"cf-connecting-ip": "198.51.100.32"}).status_code == 200
        assert railway.get("/health", headers=direct | {"cf-connecting-ip": "198.51.100.33"}).status_code == 429


def test_internals_are_admin_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    app = create_app(settings, start_scheduler=False)
    admin = {"authorization": "Bearer s3cret"}
    with TestClient(app, client=("198.51.100.4", 1)) as outsider:
        for path in ("/runs", "/sources", "/maintenance/retention"):
            assert outsider.get(path).status_code == 401, path
            assert outsider.get(path, headers=admin).status_code == 200, path
        assert outsider.get("/health").json().keys() == {"status"}
        assert "checks" in outsider.get("/health", headers=admin).json()


def test_scrapers_are_slowed_and_scripts_refused(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.security.data_rate_limit_per_minute = 2
    settings.security.blocked_bots = ["python-requests"]
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, client=("198.51.100.3", 1)) as c:
        assert c.get("/events?page=41").status_code == 400  # no paging through every event
        assert [c.get("/events").status_code for _ in range(2)] == [200, 429]  # the listing data has its own, lower limit
        assert c.get("/health").status_code == 200  # the rest of the site doesn't
        assert c.get("/health", headers={"user-agent": "python-requests/2.32"}).status_code == 200  # health probes pass
        assert c.get("/", headers={"user-agent": "python-requests/2.32"}).status_code == 403
    # Script clients: pages and llms.txt open (AI assistants browse with them), the data API refused.
    settings.security.blocked_bots, settings.security.blocked_clients = [], ["curl/"]
    with TestClient(create_app(settings, start_scheduler=False), client=("198.51.100.4", 1)) as c:
        assert c.get("/llms.txt", headers={"user-agent": "curl/8.5"}).status_code == 200
        assert c.get("/events", headers={"user-agent": "curl/8.5"}).status_code == 403


ADMIN = {"authorization": "Bearer s3cret"}
# The only writes a visitor may send: sponsored views / clicks, and "who's coming?" plans and answers.
PUBLIC_WRITES = {"/events/sponsored/hit", "/plans", "/plans/{plan_id}/votes", "/plans/{plan_id}/events"}
# A signed-in user's own writes: refused without a session cookie (403 first when not from our pages).
USER_WRITES = ("/api/me", "/api/auth/")


def _open_app(tmp_path: Path) -> Any:
    """Token set, no rate limits or bans in the way of a test that sends many requests."""
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    settings.security.rate_limit_per_minute = settings.security.data_rate_limit_per_minute = 100_000
    settings.security.admin_rate_limit_per_minute = 100_000  # the enumeration below asks every admin route
    settings.security.ban_after_strikes = 100_000
    return create_app(settings, start_scheduler=False)


def test_every_write_and_admin_route_needs_the_token(tmp_path: Path) -> None:
    """A route that changes state, or one under /admin/api, is refused without the token:
    enumerated from the app, so a route added later without AdminDep fails here."""
    app = _open_app(tmp_path)
    with TestClient(app, client=("198.51.100.20", 1)) as outsider:
        checked = 0
        # An included router is one entry in app.routes; its own routes carry the full path.
        routes = [r for top in app.routes for r in getattr(getattr(top, "original_router", None), "routes", [top])]
        for route in routes:
            if not isinstance(route, APIRoute) or route.path in PUBLIC_WRITES:
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            for method in route.methods - {"HEAD"}:
                if method == "GET" and not route.path.startswith("/admin/api"):
                    continue
                got = outsider.request(method, path, json={})
                refused = {401, 403} if route.path.startswith(USER_WRITES) else {401}
                assert got.status_code in refused, f"{method} {route.path} answered {got.status_code} without a token"
                checked += 1
        assert checked > 20  # the enumeration really found the admin routes


def test_admin_without_token_is_this_machine_only(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path), start_scheduler=False)  # no admin.token
    with TestClient(app) as local, TestClient(app, client=("198.51.100.21", 1)) as outsider:
        assert local.get("/admin/api/logging").status_code == 200
        # Behind a reverse proxy every request comes from loopback: a forwarded one is never local.
        assert local.get("/admin/api/logging", headers={"x-forwarded-for": "198.51.100.21"}).status_code == 403
        assert local.get("/admin/api/logging", headers={"x-real-ip": "1.2.3.4"}).status_code == 403
        assert outsider.get("/admin/api/logging").status_code == 403


def test_admin_config_never_shows_secrets() -> None:
    secrets = ["hunter2", "evomi-pass", "il-pass", "feed-key", "partner-key", "s3cret"]
    settings = Settings(
        database={"url": "postgresql://app:hunter2@db.internal:5432/spotx"},
        http={
            "headers": {"x-key": "partner-key"},
            "proxy": "http://user:evomi-pass@core.evomi.com:1000",
            "source_proxy": "http://a:evomi-pass@p1:1,http://b:evomi-pass@p2:2",
            "israel_proxy": "http://il:il-pass@1.2.3.4:8080",
            "israel_proxy_feed": "https://proxylist.example/api?key=feed-key",
        },
        admin={"token": "s3cret"},
    )
    shown = json.dumps(_mask(settings))
    assert not [s for s in secrets if s in shown], shown
    assert "core.evomi.com" in shown  # the host stays, to see which proxy is set


def test_backup_names_cannot_leave_the_backup_folder(tmp_path: Path) -> None:
    app = _open_app(tmp_path)
    with TestClient(app, client=("198.51.100.22", 1)) as c:
        for name in ("..%2Fsettings.yaml", "..%5Csettings.yaml", "%2e%2e%2f%2e%2e%2fpyproject.toml"):
            got = c.get(f"/admin/api/config/backups/{name}", headers=ADMIN)
            assert got.status_code == 404, (name, got.status_code)


def test_ticket_redirect_only_goes_to_web_pages(tmp_path: Path) -> None:
    """/go/ is the one redirect the site makes: never to a script URL or an unknown id."""
    settings = settings_for(tmp_path)
    soon = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("ok", soon), event("bad", soon)])
    with db.session() as session:
        session.get(EventRow, "test:bad").url = "javascript:alert(document.cookie)"
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, follow_redirects=False) as c:
        ok = c.get("/go/test:ok")
        assert ok.status_code == 302 and ok.headers["location"].startswith("https://example.com/ok")
        assert "no-store" in ok.headers["cache-control"]
        assert c.get("/go/test:bad").status_code == 404
        assert c.get("/go/https://evil.example/").status_code == 404  # an id, never a URL to follow


def test_cors_lets_only_the_site_read_and_never_write(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings, start_scheduler=False)
    own = settings.site.base_url.rstrip("/")
    with TestClient(app) as c:
        assert "access-control-allow-origin" not in c.get("/events", headers={"origin": "https://evil.example"}).headers
        assert c.get("/events", headers={"origin": own}).headers["access-control-allow-origin"] == own
        preflight = c.options("/events/sponsored/hit", headers={"origin": own, "access-control-request-method": "POST"})
        assert preflight.status_code == 400  # GET only, even from the site itself


def test_hostile_input_is_refused_not_crashed_on(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    seed(settings, [event("a", datetime.now(UTC) + timedelta(days=2))])
    app = create_app(settings, start_scheduler=False)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/events/' OR '1'='1").status_code == 404
        assert c.get("/events", params={"q": "'; DROP TABLE events;--"}).status_code == 200
        assert c.get("/events/test:a").status_code == 200  # the table is still there
        assert c.get("/events", params={"sort": "id; DELETE"}).status_code == 422
        assert len(c.get("/events", params={"size": 10_000}).json()["items"]) <= settings.api.max_page_size
        assert c.get("/locate", params={"lat": 999, "lng": 0}).status_code == 422
        assert c.get("/images/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
        assert c.get("/changes", params={"kind": ["x"] * 500}).status_code == 422
        assert c.post("/events/sponsored/hit", json={"event_id": "x" * 10_000, "kind": "view"}).status_code == 422
        assert c.post("/events/sponsored/hit", json={"event_id": "test:a", "kind": "steal"}).status_code == 422
        assert c.delete("/events/test:a").status_code == 405


def test_event_page_escapes_scraped_text(tmp_path: Path) -> None:
    """Titles and descriptions come from other sites: never markup in our page."""
    settings = settings_for(tmp_path)
    xss = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
    soon = datetime.now(UTC) + timedelta(days=2)
    db = seed(settings, [event("x", soon, title=f"Party {xss}", description=xss, venue_name=xss)])
    template = '<html lang="he" dir="rtl"><head><!--seo--></head><body><div id="root"></div></body></html>'
    with db.session(write=False) as session:
        page = seo.event_page(session, settings, "he", "test:x")
    assert page is not None
    html = seo.render(template, page, settings)
    assert "<script>alert" not in html and "<img src=x" not in html
    # Only JSON-LD scripts, each closed where it should be (no "<" inside them at all).
    assert all(json.loads(s) for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL))


def test_banned_visitor_gets_no_cached_page(tmp_path: Path) -> None:
    """Cached pages answer before routing, but still inside the security middleware."""
    settings = settings_for(tmp_path)
    settings.security.ban_after_strikes = 1
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as local, TestClient(app, client=("198.51.100.23", 1)) as outsider:
        assert local.get("/events").status_code == 200  # now cached
        outsider.get("/.git/config")  # a probe: banned
        assert outsider.get("/events").status_code == 403


@pytest.mark.skipif(not (Path(__file__).parents[1] / "web/dist/index.html").is_file(), reason="needs the web build")
def test_host_header_does_not_poison_cached_pages(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path), start_scheduler=False)
    with TestClient(app) as c:
        poisoned = c.get("/privacy", headers={"host": "evil.example", "x-forwarded-host": "evil.example"})
        assert "evil.example" not in poisoned.text
        assert "evil.example" not in c.get("/privacy").text
