"""What an alert looks for: a subset of the site's filters (web/src/store.ts Filters). Relative dates stay
relative ("weekend" is always the coming weekend) and become ``build_event_filters`` arguments when matched."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

from partylinks.config import Settings

WHEN = ("all", "weekend", "week", "month")  # "tonight" and single days are not ongoing: an alert keeps looking


class Criteria(BaseModel):
    q: str = Field("", max_length=100)  # an artist, a venue or a word
    genres: list[str] = Field(default_factory=list, max_length=20)
    regions: list[str] = Field(default_factory=list, max_length=20)
    areas: list[str] = Field(default_factory=list, max_length=20)
    city: str | None = Field(None, max_length=60)
    slots: list[str] = Field(default_factory=list, max_length=4)
    freeOnly: bool = False
    discountOnly: bool = False
    when: str = "all"  # a preset, or YYYY-MM-DD~YYYY-MM-DD

    @field_validator("q")
    @classmethod
    def _q(cls, v: str) -> str:
        v = " ".join(v.split())
        return v if len(v) >= 2 else ""  # the site searches 2+ letters: one letter is no word (else: every event)

    @field_validator("when")
    @classmethod
    def _when(cls, v: str) -> str:
        if v in WHEN:
            return v
        start, sep, end = v.partition("~")
        if not sep or date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError("when: a preset (all, weekend, week, month) or a date range YYYY-MM-DD~YYYY-MM-DD")
        return v

    def is_empty(self) -> bool:
        return self.model_dump(exclude={"when"}) == Criteria().model_dump(exclude={"when"})

    def digest(self) -> str:
        """The same criteria, the same digest: the matcher runs each distinct set once."""
        data = self.model_dump()
        for key in ("genres", "regions", "areas", "slots"):
            data[key] = sorted(data[key])
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def params(self, today: date) -> dict[str, Any]:
        """``build_event_filters`` keyword arguments: the same mapping as filterParams in web/src/Filters.tsx."""
        p: dict[str, Any] = {
            "q": self.q if len(self.q) >= 2 else None,
            "genre": self.genres or None,
            "region": self.regions or None,
            "area": self.areas or None,
            "city": self.city or None,
            "time_slot": self.slots or None,
            "free_entry": True if self.freeOnly else None,
            "discount": True if self.discountOnly else None,
        }

        def end(days: int) -> datetime:
            return datetime.combine(today + timedelta(days=days), time(23, 59, 59))

        if self.when == "weekend":
            p |= {"weekend": True, "date_to": end(7)}
        elif self.when == "week":
            p["date_to"] = end(6)
        elif self.when == "month":
            p["date_to"] = end(29)
        elif "~" in self.when:
            a, b = self.when.split("~")
            p |= {
                "date_from": datetime.combine(date.fromisoformat(a), time.min),
                "date_to": datetime.combine(date.fromisoformat(b), time(23, 59, 59)),
            }
        return {k: v for k, v in p.items() if v is not None}


def unknown(criteria: Criteria, cfg: Settings) -> list[str]:
    """Values the site's filters don't offer (an alert on them could never match)."""
    e = cfg.enrichment
    genres = set(e.genres) | set(e.genre_families) | set(e.genre_groups)
    regions = {"tel_aviv"} | {r.key for r in e.regions}
    areas = {a.key for a in e.areas}
    slots = {s.name for s in e.time_slots}
    bad = [f"genre {g}" for g in criteria.genres if g not in genres]
    bad += [f"region {r}" for r in criteria.regions if r not in regions]
    bad += [f"area {a}" for a in criteria.areas if a not in areas]
    bad += [f"time slot {s}" for s in criteria.slots if s not in slots]
    return bad
