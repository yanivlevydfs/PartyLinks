"""The artists index's filter (partylinks.artist_filter): each artist's genres, categories and places."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from tests.test_storage_api import event, seed, settings_for


def test_artist_tags_keep_what_most_of_its_events_are(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.site.artists_verified_only = False  # no curated profiles here: every lineup name is listed
    night = datetime.combine(datetime.now(ZoneInfo(settings.timezone)).date() + timedelta(days=1), time(23),
                             ZoneInfo(settings.timezone))  # fmt: skip
    seed(settings, [
        *(event(f"t{i}", night + timedelta(days=i), title=f"Techno {i}", artists=["DJ Alpha"], source_genres=["techno"])
          for i in range(3)),
        event("j", night + timedelta(days=5), title="Jazz night", artists=["DJ Alpha", "Sax Man"], source_genres=["jazz"]),
        event("b", night + timedelta(days=6), title="Berlin show", artists=["Sax Man"], source_genres=["jazz"], country="DE"),
    ])  # fmt: skip
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        tags = client.get("/artists/tags").json()
    alpha, sax = tags["dj alpha"], tags["sax man"]
    assert "techno" in alpha["g"] and "g_parties" in alpha["g"]
    assert "jazz" not in alpha["g"]  # one night of four is not what DJ Alpha plays
    assert "jazz" in sax["g"] and "g_live" in sax["g"]
    assert "DE" in sax["w"]
