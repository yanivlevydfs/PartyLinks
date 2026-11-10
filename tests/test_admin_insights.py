"""Admin insights: the features and quality tabs' counts, on the embedded PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_alerts import OURS, FakeGoogle, _app, sign_in


def test_features_and_quality_count_what_users_and_sources_did(tmp_path: Path, fake: FakeGoogle) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as user, TestClient(app) as admin:
        sign_in(user, fake)
        user.post("/api/me/alerts", json={"criteria": {"q": "techno"}, "instant": True}, headers=OURS)
        sign_in(admin, fake, email="boss@spotx.test", sub="g-admin")
        features = admin.get("/admin/api/insights/features?days=7").json()
        assert features["alerts"]["active"] == 1 and features["alerts"]["new"] == 1
        assert features["users"]["total"] == 2 and features["users"]["google"] == 2
        assert features["saves"]["total"] == 0 and features["plans"]["total"] == 0
        quality = admin.get("/admin/api/insights/quality").json()
        [row] = quality
        assert row["upcoming"] == 3 and set(row["missing"]) == {
            "map",
            "genre",
            "venue",
            "city",
            "picture",
            "price",
            "area",
        }
