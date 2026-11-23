"""GO-OUT: a removed event's page is gone, not a parse failure."""

from __future__ import annotations

from partylinks.scrapers.goout import is_removed


def _page(props: dict[str, object]) -> dict[str, object]:
    return {"props": {"pageProps": props}}


def test_a_removed_event_page_is_gone() -> None:
    # The page of 1793615364112 (Nov 2026): 200, notFound, an event holding only an empty ticket.
    removed = _page({"event": {"Tickets": [{}]}, "code": 0, "notFound": True, "originalUrl": "1793615364112"})
    assert is_removed(removed)


def test_a_live_event_page_is_not_gone() -> None:
    assert not is_removed(_page({"event": {"_id": "abc", "Title": "x"}, "notFound": False}))
    assert not is_removed(_page({"event": {"_id": "abc"}, "notFound": True}))  # it has an event after all
