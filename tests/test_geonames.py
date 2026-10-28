"""GeoNames is read once and cached small: the same cities and names as the library's own files."""

from __future__ import annotations

from pathlib import Path

import geonamescache
import pytest

from partylinks import geonames, places


def test_cache_matches_the_library_and_is_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "geonames-test.json"
    monkeypatch.setattr(geonames, "cache_file", lambda: path)
    geonames._cities.cache_clear()
    built = geonames.israel(), geonames.world()
    assert path.is_file() and path.stat().st_size < 20_000_000  # a sliver of the 96 MB the library reads

    # The same cities as the library's files, with every name the app reads.
    lib500 = geonamescache.GeonamesCache(min_city_population=500).get_cities()
    lib15000 = geonamescache.GeonamesCache(min_city_population=15000).get_cities()
    assert {c["geonameid"] for c in built[0]} == {c["geonameid"] for c in lib500.values() if c["countrycode"] == "IL"}
    assert {c["geonameid"] for c in built[1]} == {c["geonameid"] for c in lib15000.values() if c["countrycode"] != "IL"}
    for c in built[0][:300] + built[1][:300]:
        source = (lib500 if c["countrycode"] == "IL" else lib15000)[str(c["geonameid"])]
        hebrew = [a for a in source["alternatenames"] if geonames._HEBREW.search(a)]
        assert all(h in c["alternatenames"] for h in hebrew) and c["timezone"] == source["timezone"]

    # A later start reads the file only: no library load.
    geonames._cities.cache_clear()
    monkeypatch.setattr(geonames, "build", lambda: pytest.fail("the cache file must be read, not rebuilt"))
    assert (geonames.israel(), geonames.world()) == built
    geonames._cities.cache_clear()  # leave the app's own cache file for the other tests


def test_places_answer_as_before() -> None:
    places.israeli_places.cache_clear()
    places._world_cities.cache_clear()
    towns = {p.he for p in places.israeli_places(2000)}
    assert {"תל אביב-יפו", "באר שבע", "יהוד"} & towns  # Hebrew names from the cache
    assert places.city_abroad("פריז", "FR")[1][0] == "Paris" and places.city_zone("ניו יורק", "US") == "America/New_York"
    assert places.city_abroad("Vaduz", "LI") is not None  # a capital under 15,000: in cities15000, kept
