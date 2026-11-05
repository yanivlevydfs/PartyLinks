"""Geocoder queries built from what sellers write in their address / venue fields."""

from types import SimpleNamespace

from partylinks.geo import candidates


def place(address: str | None = None, venue: str | None = None, city: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(address=address, venue_name=venue, city=city, in_target_city=False)


def test_ogo_parenthesised_venue_and_english_city() -> None:
    queries = candidates(place("(Rotch) Rothschild St 64, Rishon LeZion", city="ראשון לציון"))
    assert "Rothschild St 64, Rishon LeZion" in queries  # the bare English address: Nominatim's hit
    assert not any("(Rotch)" in q for q in queries)


def test_street_inside_the_venue_field() -> None:
    queries = candidates(place(venue="ויצו רחוב ראש פינה 12", city="רמת גן"))
    assert any(q.startswith("ראש פינה 12") for q in queries)


def test_country_alone_is_no_address() -> None:
    assert not any(q.startswith("Israel") for q in candidates(place("Israel")))
