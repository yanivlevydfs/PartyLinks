from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from partylinks.api import create_app
from tests.test_storage_api import settings_for


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
        assert b.get("/admin/api/overview", headers={"authorization": "Bearer nope"}).status_code == 401
        assert b.get("/admin/api/overview", headers={"authorization": "Bearer nope"}).status_code == 401
        assert b.get("/admin/api/overview", headers={"authorization": "Bearer s3cret"}).status_code == 403  # banned


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
