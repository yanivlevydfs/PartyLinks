"""Request checks for the account routes: per-IP rate limits and our-pages-only writes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from partylinks.accounts.config import RateLimitSettings


def limited(bucket: str, limit: str) -> Any:
    """At most ``accounts.rate_limits.<limit>`` calls a minute per IP (read per request, so the admin's
    change applies at once), on the security middleware's counters."""
    if limit not in RateLimitSettings.model_fields:  # a typo fails at import, not on a visitor's request
        raise ValueError(f"no accounts.rate_limits.{limit}")

    def check(request: Request) -> None:
        guard = getattr(request.app.state, "guard", None)
        ip = request.client.host if request.client else "unknown"
        per_minute = getattr(request.app.state.settings.accounts.rate_limits, limit)
        if guard is not None and guard.over_limit(ip, bucket, per_minute, time.monotonic()):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests, try again in a minute")

    return Depends(check)


def same_origin(request: Request) -> None:
    """Writes come from our own pages: our header (a cross-site form can't set it) and, when the
    browser sends one, our Origin."""
    base = request.app.state.settings.site.base_url.rstrip("/")
    origin = request.headers.get("origin")
    if request.headers.get("x-requested-with") != "spotx" or (origin and origin.rstrip("/") != base):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")


SameOrigin = Depends(same_origin)
