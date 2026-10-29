from __future__ import annotations

import os

import pytest

# Every test runs on PostgreSQL: PARTYLINKS_TEST_PG_URL when set, else an embedded server (pgserver)
# started once per run in a temporary directory. Each test starts from an empty database, and gets it
# through PARTYLINKS_DATABASE__URL (Settings() reads it; Database(os.environ[...]) for a bare one).


@pytest.fixture(scope="session")
def _test_database(tmp_path_factory: pytest.TempPathFactory) -> str:
    from partylinks.config import DatabaseSettings
    from partylinks.localpg import local_url

    url = os.environ.get("PARTYLINKS_TEST_PG_URL")
    return url or local_url(DatabaseSettings(local_dir=str(tmp_path_factory.mktemp("pg")), local_database="partylinks_test"))


@pytest.fixture(autouse=True)
def _empty_database(_test_database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from partylinks.db import Base, postgres_url

    monkeypatch.setenv("PARTYLINKS_DATABASE__URL", _test_database)
    engine = create_engine(postgres_url(make_url(_test_database)))
    Base.metadata.drop_all(engine)
    engine.dispose()
    from partylinks.seo import _facets

    _facets().clear()  # page lists of the previous test's events


@pytest.fixture(autouse=True)
def _no_live_israeli_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """`israel_proxy` sources would fetch the live proxy feed and dial real proxies."""
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY_FEED", "")
    monkeypatch.setenv("PARTYLINKS_HTTP__ISRAEL_PROXY", "")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> object:
    """Google's side of "Continue with Google" (tests/test_accounts.py FakeGoogle): set ``.info`` to sign in."""
    from partylinks.accounts import google
    from tests.test_accounts import FakeGoogle

    g = FakeGoogle()
    monkeypatch.setattr(google, "client", lambda request: g)
    return g
