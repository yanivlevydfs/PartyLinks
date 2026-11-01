"""At most N messages a minute per chat; past that the bot says "wait" once and ignores the rest."""

from __future__ import annotations

import time
from collections import defaultdict, deque

WINDOW = 60.0
MAX_TRACKED = 10_000  # chats remembered at once; idle ones are dropped past this


class Flood:
    """ponytail: in this process's memory (one web process serves the bot); shared state if it ever scales out."""

    def __init__(self) -> None:
        self.seen: dict[str, deque[float]] = defaultdict(deque)
        self.warned: set[str] = set()

    def check(self, chat: str, limit: int, now: float | None = None) -> str:
        """"ok", "warn" (the first message over the limit: say so) or "drop"."""
        now = time.monotonic() if now is None else now
        window = self.seen[chat]
        while window and window[0] <= now - WINDOW:
            window.popleft()
        if len(window) >= limit:
            if chat in self.warned:
                return "drop"
            self.warned.add(chat)
            return "warn"
        window.append(now)
        self.warned.discard(chat)
        if len(self.seen) > MAX_TRACKED:
            for key in [k for k, w in self.seen.items() if not w or w[-1] <= now - WINDOW]:
                del self.seen[key]
        return "ok"
