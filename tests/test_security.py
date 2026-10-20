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
