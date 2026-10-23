"""Israeli towns from GeoNames: spelling and map radius for towns the config does not list."""

from __future__ import annotations

import time

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.places import PlaceIndex, all_cities, israeli_places

SETTINGS = Settings()
ENRICHER = Enricher(SETTINGS.enrichment, SETTINGS.timezone)


def test_any_israeli_town_in_the_city_field_or_address() -> None:
    city = ENRICHER.canonical_city
    assert city(None, "החרושת 2 יהוד", None) == "יהוד"
    assert city("Even Yehuda", None, None) == "אבן יהודה"
    assert city(None, "השריון 1, נס ציונה", None) == "נס ציונה"  # a configured city keeps its spelling
    assert city(None, "רחוב עומר 5, באר שבע", None) == "באר שבע"  # the last place named wins
    assert city(None, None, "מגדל המים") is None  # a venue name is not searched for small towns


def test_towns_have_plain_names_a_centre_and_a_radius() -> None:
    towns = {t.en: t for t in israeli_places(SETTINGS.enrichment.places_min_population)}
    savyon = towns["Savyon"]
    assert savyon.he == "סביון" and savyon.center and 1500 < savyon.radius_m < 4000  # no niqqud
    assert all(t.he and t.center for t in towns.values())
    configured = {c.he for c in SETTINGS.enrichment.cities}
    every = all_cities(SETTINGS.enrichment.cities, SETTINGS.enrichment.places_min_population)
    assert [c.he for c in every[: len(configured)]] == [c.he for c in SETTINGS.enrichment.cities]
    assert "תל אביב יפו" not in {c.he for c in every[len(configured) :]}  # no duplicate of a configured city


def test_lookup_stays_fast_at_production_size() -> None:
    index = PlaceIndex(all_cities(SETTINGS.enrichment.cities, SETTINGS.enrichment.places_min_population))
    start = time.perf_counter()
    for _ in range(30_000):
        index.find("מועדון הבארבי, דרך קיבוץ גלויות 52, תל אביב-יפו")
    assert time.perf_counter() - start < 3  # ~28k events are re-read after every source run
