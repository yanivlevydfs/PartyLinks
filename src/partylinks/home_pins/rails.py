"""Pinning: see the package docstring."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, or_

from partylinks.db import EventRow


def pin(
    rails: dict[str, list[Any]],
    filters: dict[str, list[ColumnElement[bool]]],
    upcoming: list[ColumnElement[bool]],
    load: Callable[[list[ColumnElement[bool]], int], list[Any]],
    refs: list[str],
) -> None:
    """Put every pinned event first in Trending and in each row it matches (``filters`` by row key)."""
    for ref in reversed(refs):  # the first pin ends up first
        this = or_(EventRow.slug == ref, EventRow.id == ref)
        for key, where in {**filters, "trending": upcoming}.items():
            if key in rails and (found := load([*where, this], 1)):
                card = found[0].model_copy(update={"pinned": True})
                rails[key] = [card, *(e for e in rails[key] if e.id != card.id)]
