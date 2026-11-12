from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete

from partylinks.api import create_app
from partylinks.config import get_settings
from partylinks.config_store import store
from partylinks.config_store.models import ConfigOverrideRow
from partylinks.config_store.source import set_active
from tests.test_storage_api import settings_for

ADMIN = {"authorization": "Bearer s3cret"}


def _clean(db) -> None:
    with db.session() as session:
        session.execute(delete(ConfigOverrideRow))
    set_active({})
    get_settings.cache_clear()


def test_overrides_merge_over_the_file_and_survive_a_reload(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert isinstance(client.app, FastAPI)
        db = client.app.state.db
        _clean(db)
        try:
            base = get_settings()
            put = client.put("/admin/api/config/overrides", headers=ADMIN, json={
                "yaml": "retention:\n  assumed_duration_hours: 4\nsources:\n  meety:\n    enabled: false\n", "note": "test"})  # fmt: skip
            assert put.status_code == 200, put.text
            body = put.json()
            assert body["saved"] and {k["key"]: k["value"] for k in body["keys"]} == {
                "retention.assumed_duration_hours": 4, "sources.meety.enabled": False}  # fmt: skip
            # Deep merge: the rest of each block still comes from the file.
            live = get_settings()
            assert live.retention.assumed_duration_hours == 4 and live.retention.cron == base.retention.cron
            assert live.sources["meety"] is not None and base.sources["meety"] is not None
            assert (
                live.sources["meety"]["enabled"] is False
                and live.sources["meety"]["cron"] == base.sources["meety"]["cron"]
            )
            assert len(live.sources) == len(base.sources)
            # A restart (or the other process) reads them back from the database.
            set_active({})
            assert store.load(db).retention.assumed_duration_hours == 4

            reset = client.delete("/admin/api/config/overrides/key/retention.assumed_duration_hours", headers=ADMIN)
            assert [k["key"] for k in reset.json()["keys"]] == ["sources.meety.enabled"]
            assert get_settings().retention.assumed_duration_hours == base.retention.assumed_duration_hours
            versions = client.get("/admin/api/config/overrides", headers=ADMIN).json()["versions"]
            assert [v["note"] for v in versions] == ["reset retention.assumed_duration_hours", "test"]
        finally:
            _clean(db)


def test_bad_or_locked_overrides_are_refused(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert isinstance(client.app, FastAPI)
        db = client.app.state.db
        _clean(db)
        try:
            for text in (
                "retention:\n  assumed_duration_hours: -1\n",
                "database:\n  url: x\n",
                "nosuch: 1\n",
                "- a list\n",
            ):
                check = client.post("/admin/api/config/overrides/validate", headers=ADMIN, json={"yaml": text}).json()
                assert not check["ok"], text
                assert client.put("/admin/api/config/overrides", headers=ADMIN, json={"yaml": text}).status_code == 422
            assert client.get("/admin/api/config/overrides").status_code in (401, 403)  # admin only
            assert store.latest(db) == {}
        finally:
            _clean(db)


def test_helpers() -> None:
    data = {"a": {"b": 1, "c": {"d": 2}}, "e": [1]}
    assert store.flatten(data) == {"a.b": 1, "a.c.d": 2, "e": [1]}
    assert store.without(data, "a.c.d") == {"a": {"b": 1}, "e": [1]}
    assert store.merge({"a": {"b": 0, "x": 9}, "e": [0, 0]}, data) == {"a": {"b": 1, "x": 9, "c": {"d": 2}}, "e": [1]}
    assert store.lookup(data, "a.c.d") == 2 and store.lookup(data, "a.zz") is None


def test_the_home_rows_are_changed_in_the_config_tab(tmp_path: Path) -> None:
    """home_rails: a changed row round-trips through the overrides and /events/rails shows it after apply."""
    settings = settings_for(tmp_path)
    settings.admin.token = SecretStr("s3cret")
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        assert isinstance(client.app, FastAPI)
        db = client.app.state.db
        _clean(db)
        try:
            for bad in ("home_rails:\n  rows: [{key: x, genre: [no_such_genre]}]\n",
                        "home_rails:\n  rows: [{key: x}, {key: x}]\n"):  # fmt: skip
                assert not client.post("/admin/api/config/overrides/validate", headers=ADMIN, json={"yaml": bad}).json()["ok"]
            text = "home_rails:\n  rows: [{key: hitech, genre: [hi_tech]}, {key: brand_new, genre: [jazz]}]\n"
            assert client.post("/admin/api/config/overrides/validate", headers=ADMIN, json={"yaml": text}).json()["ok"]
            assert client.put("/admin/api/config/overrides", headers=ADMIN, json={"yaml": text}).status_code == 200
            rails = client.get("/events/rails").json()["rails"]
            assert {"hitech", "brand_new"} <= set(rails) and "electronic" not in rails  # the list replaces the file's
            assert get_settings().home_rails.pool == store.file_data()["home_rails"]["pool"]  # the rest of the block stays
        finally:
            _clean(db)
